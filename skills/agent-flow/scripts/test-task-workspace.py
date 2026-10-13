#!/usr/bin/env python3
"""Full-inventory and descriptor-safe workspace regressions."""
import os
from pathlib import Path
import tempfile
import unittest
import shutil
import json
import journal_io
import sys
import shlex
import subprocess
import signal
from unittest.mock import patch

import task_workspace as workspace


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "source"
        self.root.mkdir()

    def test_wc02_ignored_unannounced_and_empty_directories(self):
        (self.root / ".gitignore").write_text("ignored/\n")
        (self.root / "ignored").mkdir()
        (self.root / "ignored/hidden").write_bytes(b"not declared")
        (self.root / "empty").mkdir()
        baseline = workspace.scan_tree(self.root)
        (self.root / "ignored/hidden").write_bytes(b"changed")
        (self.root / "unannounced").write_bytes(b"new")
        (self.root / "empty").rmdir()
        delta = workspace.manifest_delta(baseline, workspace.scan_tree(self.root))
        self.assertEqual(set(delta), {"ignored/hidden", "unannounced", "empty"})

    def test_wc02_mode_only_changes_manifest_and_delta(self):
        path = self.root / "script"
        path.write_bytes(b"same bytes")
        path.chmod(0o644)
        before = workspace.scan_tree(self.root)
        path.chmod(0o755)
        after = workspace.scan_tree(self.root)
        self.assertNotEqual(workspace.manifest_digest(before), workspace.manifest_digest(after))
        self.assertEqual(set(workspace.manifest_delta(before, after)), {"script"})

    def test_wc03_copy_names_links_and_independent_hardlinks(self):
        (self.root / "space\nЮникод").write_bytes(b"content")
        (self.root / "hardlink").hardlink_to(self.root / "space\nЮникод")
        (self.root / "link").symlink_to("space\nЮникод")
        (self.root / "dangling").symlink_to("missing")
        destination = self.root.parent / "copy"
        destination.mkdir()
        before = workspace.scan_tree(self.root, copy_to=destination)
        self.assertEqual(before, workspace.scan_tree(destination))
        self.assertNotEqual((destination / "hardlink").stat().st_ino, (self.root / "hardlink").stat().st_ino)
        self.assertNotEqual((destination / "hardlink").stat().st_ino, (destination / "space\nЮникод").stat().st_ino)

    def test_wc03_refuses_escape_special_unreadable_and_nested_repo(self):
        for kind in ("escape", "fifo", "unreadable", "nested"):
            path = self.root / "bad"
            with self.subTest(kind=kind):
                if kind == "escape": path.symlink_to("../outside")
                elif kind == "fifo": os.mkfifo(path)
                elif kind == "unreadable": path.write_bytes(b"secret"); path.chmod(0)
                else: path.mkdir(); (path / ".git").mkdir()
                with self.assertRaises(workspace.WorkspaceError): workspace.scan_tree(self.root)
                if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
                else: path.unlink()

    def test_wc03_indirect_symlink_escape_is_rejected_without_following(self):
        (self.root / "alias").symlink_to(".")
        (self.root / "escape").symlink_to("alias/..")
        with self.assertRaisesRegex(workspace.WorkspaceError, "escaping symlink chain"):
            workspace.scan_tree(self.root)

    def test_wc01_file_swap_refuses_before_external_read(self):
        path = self.root / "file"
        path.write_bytes(b"owned")
        outside = self.root.parent / "outside"
        outside.write_bytes(b"EXTERNAL-MARKER")
        for kind in ("symlink", "regular", "fifo"):
            with self.subTest(kind=kind):
                path.write_bytes(b"owned")
                buffers = []
                fired = []
                def barrier(phase, context):
                    if phase == "source-before-open" and context["path"] == "file" and not fired:
                        fired.append(True)
                        path.unlink()
                        if kind == "symlink": path.symlink_to(outside)
                        elif kind == "regular": shutil.copyfile(outside, path)
                        else: os.mkfifo(path)
                    if phase == "source-read-buffer": buffers.append(context["data"])
                with self.assertRaises(workspace.WorkspaceError): workspace.scan_tree(self.root, barrier=barrier)
                self.assertEqual(fired, [True])
                self.assertNotIn(b"EXTERNAL-MARKER", b"".join(buffers))
                path.unlink()

    def test_wc01_opened_parent_swap_restore_never_redirects(self):
        parent = self.root / "parent"
        parent.mkdir()
        (parent / "file").write_bytes(b"owned")
        outside = self.root.parent / "external-dir"
        outside.mkdir()
        (outside / "file").write_bytes(b"EXTERNAL-MARKER")
        fired, buffers = [], []
        def barrier(phase, context):
            if phase == "source-directory-opened" and context["path"] == "parent":
                fired.append(True)
                parent.rename(self.root / "parked")
                parent.symlink_to(outside)
                parent.unlink()
                (self.root / "parked").rename(parent)
            if phase == "source-read-buffer": buffers.append(context["data"])
        with self.assertRaises(workspace.WorkspaceError): workspace.scan_tree(self.root, barrier=barrier)
        self.assertEqual(fired, [True])
        self.assertNotIn(b"EXTERNAL-MARKER", b"".join(buffers))


class WorkflowTests(unittest.TestCase):
    def test_phase1_retained_os_observations_do_not_bind_content(self):
        prepared = workspace.prepare(self.run, self.source, self.destination)
        (Path(prepared["working_root"]) / "tracked").write_bytes(b"new product")
        sealed = workspace.seal(self.run)
        retained = {**prepared, "seal": dict(sealed)}
        for key in ("bundle_identity", "attempt_identity", "baseline_identity", "working_identity"):
            retained[key] = [-1, -2]
        for key in ("container_identity", "candidate_identity"):
            retained["seal"][key] = [-3, -4]
        workspace.verify_candidate(retained)
        self.assertEqual(workspace.observed_tree(Path(retained["working_root"]), retained["baseline_manifest"],
                                                retained["working_identity"]), sealed["candidate_manifest"])
        binding = {name + "_root": str(self.source) for name in ("source", "metadata", "git", "common")}
        binding.update({name + "_identity": [-5, -6] for name in ("source", "metadata", "git", "common")})
        workspace.validate_metadata_identity(binding)
        (Path(sealed["candidate_root"]) / "tracked").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "sealed candidate changed"):
            workspace.verify_candidate(retained)

    def test_phase1_relocated_retained_roots_retry_and_scope_repack(self):
        dependency = self.root / "dependency"
        dependency.mkdir()
        (dependency / "input").write_bytes(b"dependency")
        prepared = workspace.prepare(self.run, self.source, self.destination, identifier="identity-retry")
        (Path(prepared["working_root"]) / "tracked").write_bytes(b"changed")
        sealed = workspace.seal(self.run, identifier="identity-seal")
        original = self.destination.with_name("old-bundle")
        self.destination.rename(original)
        shutil.copytree(original, self.destination, symlinks=True)
        self.assertNotEqual(list(workspace.identity(self.destination.lstat())[:2]), prepared["bundle_identity"])
        workspace.inspect(self.run)
        self.assertEqual(workspace.prepare(self.run, self.source, self.destination, identifier="identity-retry"), prepared)
        self.assertEqual(workspace.seal(self.run, identifier="identity-seal"), sealed)
        expanded = workspace.prepare(self.run, [self.source, dependency], self.destination, identifier="identity-expansion")
        self.assertEqual(Path(expanded["working_root"], "source/tracked").read_bytes(), b"changed")
        self.assertIn("source/tracked", workspace.seal(self.run)["changed_paths"])

    def test_phase1_metadata_exclusion_authorization_proof_is_still_verified(self):
        from verification_evidence import result_hash
        (self.source / ".agent-work").mkdir()
        request, source = self.metadata_fixture()
        prepared = workspace.prepare(self.run, self.source, self.destination, metadata_scope=request, session_source=source)
        (Path(prepared["working_root"]) / "tracked").write_bytes(b"changed product")
        sealed = workspace.seal(self.run)
        verification = {"result_files": sealed["changed_paths"], "initial_snapshot": None,
                        "task_scope": request["scope_ref"]}
        result_hash(self.run, verification)
        name = request["qa_proof_ref"]["path"]
        journal_io.transact(self.run, "changed-metadata-proof", {}, lambda current: ({name: b"tampered proof"}, {}))
        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            result_hash(self.run, verification)

    def test_impl018_canonical_and_noncanonical_default_reuse_and_explicit_conflict(self):
        for canonical in (False, True):
            with self.subTest(canonical=canonical):
                run = self.root / (".agent-work/runs/default" if canonical else "other-run")
                journal_io.initialize_journal(run, {}, source_root=self.source)
                snapshot = journal_io.JournalSnapshot.open(run)
                expected = self.root / ".agent-work/workspaces" / ("default-" + snapshot.run_uuid) if canonical else run / ("workspace-" + snapshot.run_uuid)
                cli = Path(__file__).with_name("task-workspace.py")
                result = subprocess.run([sys.executable, str(cli), "--run-dir", str(run), "prepare", "--source", str(self.source), "--operation-id", "default-cli"], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                prepared = json.loads(result.stdout)
                self.assertIsNotNone(journal_io.JournalSnapshot.open(run).operation_receipt("workspace-prepare-default-cli"))
                self.assertEqual(prepared["bundle"], str(expected))
                self.assertEqual(workspace.prepare(run, self.source), prepared)
                self.assertEqual(workspace.prepare(run, self.source, expected), prepared)
                with self.assertRaisesRegex(workspace.WorkspaceError, "root binding cannot change"):
                    workspace.prepare(run, self.source, self.root / "different")
                rejected = subprocess.run([sys.executable, str(cli), "--run-dir", str(run), "prepare", "--source", str(self.source), "--destination", str(self.root / "different")], text=True, capture_output=True)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("root binding cannot change", rejected.stderr)

    def test_impl018_default_parent_symlink_refused(self):
        run = self.root / ".agent-work/runs/default-link"
        journal_io.initialize_journal(run, {}, source_root=self.source)
        (self.root / ".agent-work/workspaces").symlink_to(self.source, target_is_directory=True)
        with self.assertRaises((workspace.WorkspaceError, OSError)):
            workspace.prepare(run, self.source)
        self.assertIsNone(workspace.registered_workspace(journal_io.JournalSnapshot.open(run)))

    def test_impl018_default_multi_root_uses_journal_anchor(self):
        dependency = self.root / "dependency"
        dependency.mkdir()
        workspace.git_command(dependency, "init", "-q")
        (dependency / "tracked").write_text("different relative name\n")
        prepared = workspace.prepare(self.run, [self.source, dependency])
        self.assertEqual(prepared["bundle"], str(self.run / ("workspace-" + journal_io.JournalSnapshot.open(self.run).run_uuid)))
        self.assertEqual(workspace.prepare(self.run, [self.source, dependency]), prepared)

    def test_impl018_missing_bytes_diagnostic_never_uses_live_source(self):
        prepared = workspace.prepare(self.run, self.source)
        (Path(prepared["working_root"]) / "tracked").write_text("candidate\n")
        sealed = workspace.seal(self.run)
        shutil.rmtree(sealed["candidate_root"])
        with self.assertRaisesRegex(workspace.WorkspaceError, "retained root missing; supply unverified:.*candidate"):
            workspace.inspect(self.run)
        shutil.rmtree(prepared["baseline_root"])
        with self.assertRaisesRegex(workspace.WorkspaceError, "retained root missing; supply unverified:.*baseline"):
            workspace.verify_baseline(prepared)
        self.assertEqual((self.source / "tracked").read_bytes(), b"already dirty")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        workspace.git_command(self.source, "init", "-q")
        workspace.git_command(self.source, "config", "user.name", "Fixture")
        workspace.git_command(self.source, "config", "user.email", "fixture@example.invalid")
        (self.source / "tracked").write_bytes(b"original")
        (self.source / ".gitignore").write_text("ignored/\n")
        workspace.git_command(self.source, "add", ".")
        workspace.git_command(self.source, "commit", "-qm", "fixture baseline")
        (self.source / "tracked").write_bytes(b"already dirty")
        (self.source / "ignored").mkdir()
        (self.source / "ignored/dependency").write_bytes(b"ignored baseline")
        self.run = self.root / "run"
        journal_io.initialize_journal(self.run, {}, source_root=self.source)
        self.destination = self.root / "owned"

    def test_wc01_git_metadata_disables_filters_and_inherited_git_env(self):
        marker = self.root / "filter-executed"
        (self.source / ".gitattributes").write_text("tracked filter=fixture\n")
        program = f"from pathlib import Path; import sys; Path({str(marker)!r}).write_text('executed'); sys.stdout.buffer.write(sys.stdin.buffer.read())"
        command = shlex.quote(sys.executable) + " -c " + shlex.quote(program)
        workspace.git_command(self.source, "config", "filter.fixture.clean", command)
        workspace.git_command(self.source, "config", "filter.fixture.smudge", command)
        workspace.git_command(self.source, "config", "filter.fixture.required", "true")
        metadata = workspace.git_identity(self.source)
        self.assertFalse(marker.exists(), "Git metadata must not execute source clean filters")
        with patch.dict(os.environ, {"GIT_DIR": str(self.root / "foreign"), "GIT_WORK_TREE": str(self.root),
                                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": command}):
            self.assertEqual(workspace.git_identity(self.source), metadata)
        self.assertFalse(marker.exists())

    def test_wc01_git_copy_parent_swap_never_writes_outside(self):
        outside = self.root / "outside-git-copy"
        outside.mkdir()
        fired = []
        def barrier(phase, context):
            if phase == "prepare-before-git-copy":
                attempt = Path(context["attempt"])
                attempt.rename(attempt.with_name(attempt.name + "-held"))
                attempt.symlink_to(outside)
                fired.append(phase)
        with self.assertRaises((workspace.WorkspaceError, OSError)):
            workspace.prepare(self.run, self.source, self.destination, barrier=barrier)
        self.assertTrue(fired)
        self.assertEqual(list(outside.iterdir()), [], "Git metadata write escaped pinned destination before refusal")
        self.assertIsNone(workspace.registered_workspace(journal_io.JournalSnapshot.open(self.run)))

    def test_wc01_unsafe_git_clone_control_detects_outside_write(self):
        outside, alias = self.root / "unsafe-outside", self.root / "unsafe-alias"
        outside.mkdir()
        alias.symlink_to(outside)
        workspace.git_command(alias, "-c", "protocol.file.allow=always", "clone", "--local", "--no-hardlinks", "--no-checkout", str(self.source), "working")
        self.assertTrue((outside / "working/.git").is_dir())
        with self.assertRaises(AssertionError):
            self.assertEqual(list(outside.iterdir()), [], "Git metadata write escaped pinned destination before refusal")

    def test_wc01_git_metadata_source_and_destination_races(self):
        for kind in ("head-link", "head-regular", "head-fifo", "source-parent-restored", "git-destination-parent"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                source, run, bundle, outside = root / "source", root / "run", root / "bundle", root / "outside"
                source.mkdir(); outside.mkdir()
                workspace.git_command(source, "init", "-q")
                (source / "file").write_bytes(b"owned product")
                marker = outside / "HEAD"
                marker.write_bytes(b"EXTERNAL-MARKER")
                journal_io.initialize_journal(run, {}, source_root=source)
                fired, buffers = [], []
                def barrier(phase, context):
                    if phase in {"source-read-buffer", "git-read-buffer"}:
                        buffers.append(context["data"])
                    if fired: return
                    if kind.startswith("head-") and phase == "git-before-open" and context["path"] == "HEAD":
                        target = source / ".git/HEAD"
                        target.rename(source / ".git/old-HEAD")
                        if kind == "head-link": target.symlink_to(marker)
                        elif kind == "head-fifo": os.mkfifo(target)
                        else: target.write_bytes(marker.read_bytes())
                    elif kind == "source-parent-restored" and phase == "git-file-opened" and context["path"] == "HEAD":
                        original = source / ".git"
                        original.rename(source / "held-git")
                        original.symlink_to(outside)
                        original.unlink()
                        (source / "held-git").rename(original)
                    elif kind == "git-destination-parent" and phase == "root-opened" and context.get("destination") and str(context["path"]).endswith("working/.git"):
                        working = Path(context["path"]).parent
                        working.rename(working.with_name("held-working"))
                        working.symlink_to(outside)
                    else: return
                    fired.append(phase)
                with self.assertRaises((workspace.WorkspaceError, OSError)):
                    workspace.prepare(run, source, bundle, barrier=barrier)
                self.assertTrue(fired)
                self.assertNotIn(b"EXTERNAL-MARKER", b"".join(buffers))
                self.assertEqual(list(outside.iterdir()), [marker])
                self.assertIsNone(workspace.registered_workspace(journal_io.JournalSnapshot.open(run)))
                for path in bundle.rglob("HEAD"):
                    if path.is_file() and not path.is_symlink():
                        self.assertNotIn(b"EXTERNAL-MARKER", path.read_bytes())

    def test_wc01_source_config_include_is_not_followed(self):
        external = self.root / "external-config"
        external.write_text("this is deliberately not valid Git configuration")
        with (self.source / ".git/config").open("a") as handle:
            handle.write("\n[include]\npath = " + str(external) + "\n")
        prepared = workspace.prepare(self.run, self.source, self.destination)
        config = (Path(prepared["working_root"]) / ".git/config").read_text()
        self.assertNotIn(str(external), config)
        self.assertIn("status_base64", prepared["source_metadata"])
        self.assertEqual(external.read_text(), "this is deliberately not valid Git configuration")

    def test_wc01_unsupported_git_storage_refuses_before_publication(self):
        for kind in ("format", "alternates", "promisor", "metadata-link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                source, run, destination = root / "source", root / "run", root / "owned"
                source.mkdir()
                workspace.git_command(source, "init", "-q")
                (source / "product").write_bytes(b"product")
                journal_io.initialize_journal(run, {}, source_root=source)
                if kind == "format":
                    with (source / ".git/config").open("a") as handle:
                        handle.write("\n[extensions]\nrefStorage = reftable\n")
                elif kind == "alternates":
                    (source / ".git/objects/info/alternates").write_text(str(self.source / ".git/objects") + "\n")
                elif kind == "promisor":
                    (source / ".git/objects/pack/fixture.promisor").write_bytes(b"")
                else:
                    (source / ".git/refs/unsafe").symlink_to("heads")
                with self.assertRaises(workspace.WorkspaceError): workspace.prepare(run, source, destination)
                self.assertIsNone(workspace.registered_workspace(journal_io.JournalSnapshot.open(run)))

    def test_wc01_linked_source_and_staged_dirty_state_preserved(self):
        linked = self.root / "linked"
        workspace.git_command(self.source, "gc", "--prune=never")
        workspace.git_command(self.source, "pack-refs", "--all")
        workspace.git_command(self.source, "worktree", "add", "--detach", str(linked), "HEAD")
        (linked / "tracked").write_bytes(b"staged")
        workspace.git_command(linked, "add", "tracked")
        (linked / "tracked").write_bytes(b"unstaged")
        before = workspace.git_identity(linked)
        prepared = workspace.prepare(self.run, linked, self.destination)
        self.assertEqual(before, workspace.git_identity(linked))
        self.assertEqual((Path(prepared["working_root"]) / "tracked").read_bytes(), b"unstaged")
        self.assertNotEqual(prepared["source_metadata"]["git_dir"], prepared["source_metadata"]["common_dir"])
        self.assertTrue((Path(prepared["working_root"]) / ".git").is_dir())

    def test_wc01_prepare_preserves_dirty_source_and_independent_git(self):
        before = workspace.git_identity(self.source)
        prepared = workspace.prepare(self.run, self.source, self.destination)
        self.assertEqual(workspace.git_identity(self.source), before)
        working = Path(prepared["working_root"])
        self.assertEqual((working / "tracked").read_bytes(), b"already dirty")
        self.assertEqual((working / "ignored/dependency").read_bytes(), b"ignored baseline")
        self.assertFalse((working / ".git/objects/info/alternates").exists())
        (working / "tracked").write_bytes(b"task change")
        self.assertEqual((self.source / "tracked").read_bytes(), b"already dirty")
        self.assertEqual(workspace.prepare(self.run, self.source, self.destination)["workspace_id"], prepared["workspace_id"])

    def metadata_fixture(self):
        import importlib.util
        import verification_evidence as evidence
        spec = importlib.util.spec_from_file_location("metadata_fixtures", Path(__file__).with_name("test-verification-evidence.py"))
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        source = fixtures.SyntheticSource()
        documents = {"scope.md": b"Synthetic accepted reserved runtime scope.", "qa.md": b"QA scope", "review.md": b"Reviewer scope"}
        digest = journal_io.digest(documents["scope.md"])
        request = {"namespace": "agent-flow-runtime", "authorized_source": str(self.source),
                   "scope_ref": {"path": "scope.md", "sha256": digest}}
        for index, (key, role, thread, handoff) in enumerate((
                ("qa_proof_ref", "qa-verifier", fixtures.QA_ID, "qa.md"),
                ("reviewer_proof_ref", "reviewer", fixtures.REVIEWER_ID, "review.md"))):
            answer = {"verdict": "passed", "reviewed_result_hash": digest, "handoff": handoff,
                      "handoff_sha256": journal_io.digest(documents[handoff])}
            if index: answer["qa_handoff_sha256"] = journal_io.digest(documents["qa.md"])
            source.sessions[thread] = fixtures.session(thread, role, answer, 10 + index * 10)
            completion = evidence.completed_turn(source, thread, thread + "-turn", fixtures.ROOT_ID, role)
            proof = json.dumps({"thread_id": thread, "completion_turn_id": thread + "-turn", "completion": completion}, default=str).encode()
            name = key + ".json"
            documents[name] = proof
            request[key] = {"path": name, "sha256": journal_io.digest(proof)}
        documents["delegation-summary.json"] = json.dumps({"verification": {"root_thread_id": fixtures.ROOT_ID,
                                                                           "author_thread_ids": [fixtures.ROOT_ID]}}).encode()
        journal_io.transact(self.run, "fixture-metadata-proof", {}, lambda current: (documents, {}))
        return request, source

    def test_wc02_metadata_namespace_requires_explicit_bound_acceptance(self):
        namespace = self.source / ".agent-work"
        namespace.mkdir()
        (namespace / "ordinary-product").write_bytes(b"included unless accepted namespace")
        prepared = workspace.prepare(self.run, self.source, self.destination)
        self.assertIn(".agent-work/ordinary-product", prepared["baseline_manifest"]["entries"])

    def test_wc02_accepted_metadata_scope_binding_and_spoof_rejection(self):
        namespace = self.source / ".agent-work"
        namespace.mkdir()
        (namespace / "nested").mkdir()
        (namespace / "nested/.git").mkdir()
        (self.source / "product").mkdir()
        (self.source / "product/.agent-work").mkdir()
        (self.source / "product/.agent-work/file").write_bytes(b"product")
        request, source = self.metadata_fixture()
        prepared = workspace.prepare(self.run, self.source, self.destination, metadata_scope=request, session_source=source)
        self.assertIn(".agent-work", prepared["baseline_manifest"]["exclusions"])
        self.assertNotIn(".agent-work", prepared["baseline_manifest"]["entries"])
        self.assertIn("product/.agent-work/file", prepared["baseline_manifest"]["entries"])
        self.assertEqual(prepared["metadata_scope"]["metadata_identity"], list(workspace.identity(namespace.stat())[:2]))
        self.assertEqual(prepared, workspace.prepare(self.run, self.source, self.destination, metadata_scope=request, session_source=source))
        for mutation in ("omit", "scope", "source", "both-sources", "namespace"):
            with self.subTest(mutation=mutation):
                changed = json.loads(json.dumps(request))
                actual_source = self.source
                if mutation == "omit": changed = None
                elif mutation == "scope": changed["scope_ref"]["sha256"] = "0" * 64
                elif mutation in {"source", "both-sources"}:
                    actual_source = self.root / "another"
                    if mutation == "both-sources": changed["authorized_source"] = str(actual_source)
                else: changed["namespace"] = "arbitrary-directory"
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.prepare(self.run, actual_source, self.destination, metadata_scope=changed, session_source=source)
        thread = next(iter(source.sessions))
        source.sessions[thread][-1]["payload"]["last_agent_message"] = "{}"
        with self.assertRaises(workspace.WorkspaceError):
            workspace.prepare(self.run, self.source, self.destination, metadata_scope=request, session_source=source)
        namespace.rename(self.source / "old-metadata")
        namespace.symlink_to(self.source / "old-metadata")
        with self.assertRaises(workspace.WorkspaceError): workspace.seal(self.run)

    def test_wc01_prepare_and_seal_descriptor_race_matrix(self):
        cases = ("file-link", "file-regular", "file-fifo", "parent-before", "parent-open-restored",
                 "destination-parent", "destination-file", "destination-before-file")
        for stage in ("prepare", "seal"):
            for kind in cases:
                with self.subTest(stage=stage, kind=kind), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve()
                    source = root / "source"
                    source.mkdir()
                    workspace.git_command(source, "init", "-q")
                    (source / "dir").mkdir()
                    (source / "dir/file").write_bytes(b"owned bytes")
                    outside = root / "outside"
                    outside.mkdir()
                    marker = outside / "file"
                    marker.write_bytes(b"EXTERNAL-MARKER")
                    run, destination = root / "run", root / "workspace"
                    journal_io.initialize_journal(run, {}, source_root=source)
                    active_source = source
                    if stage == "seal":
                        prepared = workspace.prepare(run, source, destination)
                        active_source = Path(prepared["working_root"])
                    before = journal_io.JournalSnapshot.open(run)
                    fired, buffers, descriptors = [], [], []
                    active = False
                    def barrier(phase, context):
                        nonlocal active
                        if phase == stage + "-before-copy": active = True
                        if not active: return
                        if phase == "source-read-buffer":
                            buffers.append(context["data"])
                            descriptors.append(workspace.identity(os.fstat(context["fd"])))
                        if fired: return
                        name = context.get("path")
                        if kind.startswith("file-") and phase == "source-before-open" and name == "dir/file":
                            target = active_source / name
                            target.unlink()
                            if kind == "file-link": target.symlink_to(marker)
                            elif kind == "file-fifo": os.mkfifo(target)
                            else: target.write_bytes(marker.read_bytes())
                        elif kind == "parent-before" and phase == "source-before-open" and name == "dir":
                            (active_source / "dir").rename(active_source / "held")
                            (active_source / "dir").symlink_to(outside)
                        elif kind == "parent-open-restored" and phase == "source-directory-opened" and name == "dir":
                            descriptors.append(workspace.identity(os.fstat(context["fd"])))
                            (active_source / "dir").rename(active_source / "held")
                            (active_source / "dir").symlink_to(outside)
                            (active_source / "dir").unlink()
                            (active_source / "held").rename(active_source / "dir")
                        elif kind == "destination-parent" and phase == "destination-directory-opened" and name == "dir":
                            parent = context["parent_fd"]
                            os.rename("dir", "held", src_dir_fd=parent, dst_dir_fd=parent)
                            os.symlink(str(outside), "dir", dir_fd=parent)
                        elif kind == "destination-file" and phase == "destination-file-opened" and name == "dir/file":
                            parent = context["parent_fd"]
                            os.unlink("file", dir_fd=parent)
                            os.symlink(str(marker), "file", dir_fd=parent)
                        elif kind == "destination-before-file" and phase == "destination-before-file" and name == "dir/file":
                            os.symlink(str(marker), "file", dir_fd=context["parent_fd"])
                        else:
                            return
                        fired.append(phase)
                    with self.assertRaises((workspace.WorkspaceError, OSError)):
                        if stage == "prepare": workspace.prepare(run, source, destination, barrier=barrier)
                        else: workspace.seal(run, barrier=barrier)
                    self.assertTrue(fired, "injection must actually execute")
                    self.assertNotIn(b"EXTERNAL-MARKER", b"".join(buffers))
                    self.assertEqual(marker.read_bytes(), b"EXTERNAL-MARKER")
                    after = journal_io.JournalSnapshot.open(run)
                    self.assertEqual((before.revision, dict(before.documents)), (after.revision, dict(after.documents)))
                    self.assertFalse((run / ".journal/views").exists())
                    for path in destination.rglob("*"):
                        if path.is_relative_to(active_source):
                            continue
                        if path.is_file() and not path.is_symlink() and path.name == "file":
                            self.assertNotIn(b"EXTERNAL-MARKER", path.read_bytes(), "staged external bytes before cleanup")

    def test_wc01_unsafe_control_detects_external_bytes(self):
        target = self.root / "unsafe"
        outside = self.root / "external"
        target.write_bytes(b"owned")
        outside.write_bytes(b"EXTERNAL-MARKER")
        target.lstat()
        target.unlink()
        target.symlink_to(outside)
        captured = target.read_bytes()
        self.assertEqual(captured, b"EXTERNAL-MARKER")
        with self.assertRaises(AssertionError):
            self.assertNotIn(b"EXTERNAL-MARKER", captured)

    def test_wc06_two_tasks_and_foreign_source_edit_remain_independent(self):
        first = workspace.prepare(self.run, self.source, self.destination)
        second_run = self.root / "second-run"
        journal_io.initialize_journal(second_run, {}, source_root=self.source)
        second = workspace.prepare(second_run, self.source, self.root / "second-workspace")
        program = "from pathlib import Path; Path(" + repr(str(self.source / "tracked")) + ").write_bytes(b'foreign edit')"
        subprocess.run([sys.executable, "-B", "-c", program], check=True)
        source_after_foreign = workspace.git_identity(self.source)
        (Path(first["working_root"]) / "first").write_bytes(b"first task")
        (Path(second["working_root"]) / "second").write_bytes(b"second task")
        a, b = workspace.seal(self.run), workspace.seal(second_run)
        self.assertEqual(a["changed_paths"], ["first"])
        self.assertEqual(b["changed_paths"], ["second"])
        self.assertEqual((self.source / "tracked").read_bytes(), b"foreign edit")
        self.assertEqual(source_after_foreign, workspace.git_identity(self.source))
        roots = [self.source, Path(first["working_root"]), Path(second["working_root"]), Path(a["candidate_root"]), Path(b["candidate_root"])]
        self.assertEqual(len({(root / "tracked").stat().st_ino for root in roots}), len(roots))
        with self.assertRaises(workspace.WorkspaceError):
            workspace.prepare(second_run, self.source, self.destination)

    def test_wc05_process_kill_prepare_seal_and_publication_replay(self):
        for stage in ("prepare", "seal"):
            for point in ("before-copy", "before-publication", "after-publication"):
                with self.subTest(stage=stage, point=point), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve()
                    source, run, destination = root / "source", root / "run", root / "owned"
                    source.mkdir()
                    workspace.git_command(source, "init", "-q")
                    (source / "product").write_bytes(b"baseline")
                    journal_io.initialize_journal(run, {}, source_root=source)
                    if stage == "seal":
                        prepared = workspace.prepare(run, source, destination)
                        (Path(prepared["working_root"]) / "new").write_bytes(b"candidate")
                    program = "\n".join([
                        "import os, signal, sys", "from pathlib import Path",
                        "sys.path.insert(0, " + repr(str(Path(__file__).parent)) + ")",
                        "import task_workspace as w",
                        "def barrier(phase, context):",
                        "    if phase == " + repr(stage + "-" + point) + ": os.kill(os.getpid(), signal.SIGKILL)",
                        ("w.prepare(Path(" + repr(str(run)) + "), Path(" + repr(str(source)) + "), Path(" + repr(str(destination)) + "), barrier=barrier)")
                        if stage == "prepare" else ("w.seal(Path(" + repr(str(run)) + "), identifier='kill-fixture', barrier=barrier)")])
                    child = subprocess.run([sys.executable, "-B", "-c", program], capture_output=True, timeout=30)
                    self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode())
                    current = workspace.registered_workspace(journal_io.JournalSnapshot.open(run))
                    committed = point == "after-publication"
                    self.assertEqual(current is not None if stage == "prepare" else "seal" in current, committed)
                    if stage == "prepare":
                        recovered = workspace.prepare(run, source, destination)
                        self.assertEqual((Path(recovered["working_root"]) / "product").read_bytes(), b"baseline")
                    else:
                        recovered = workspace.seal(run, identifier="kill-fixture")
                        self.assertEqual((Path(recovered["candidate_root"]) / "new").read_bytes(), b"candidate")
                        self.assertEqual(recovered["candidate_id"], workspace.seal(run, identifier="kill-fixture")["candidate_id"])
                    self.assertEqual((source / "product").read_bytes(), b"baseline")

    def test_wc05_retained_baseline_and_capsule_tamper_fail_closed(self):
        prepared = workspace.prepare(self.run, self.source, self.destination)
        sealed = workspace.seal(self.run)
        baseline = Path(prepared["baseline_root"]) / "tracked"
        original = baseline.read_bytes()
        baseline.write_bytes(b"corrupt original bytes")
        with self.assertRaisesRegex(workspace.WorkspaceError, "baseline changed"):
            workspace.seal(self.run)
        baseline.write_bytes(original)
        manifest = Path(sealed["candidate_root"]).parent / "manifest.json"
        manifest.write_bytes(b"{}")
        with self.assertRaisesRegex(workspace.WorkspaceError, "candidate manifest changed"):
            workspace.seal(self.run, identifier=sealed["operation_id"])

    def test_wc02_seal_full_delta_and_candidate_tamper(self):
        prepared = workspace.prepare(self.run, self.source, self.destination)
        working = Path(prepared["working_root"])
        (working / "unannounced").write_bytes(b"task created")
        (working / "ignored/dependency").chmod(0o755)
        sealed = workspace.seal(self.run)
        self.assertEqual(set(sealed["changed_paths"]), {"unannounced", "ignored/dependency"})
        self.assertEqual(workspace.seal(self.run)["candidate_id"], sealed["candidate_id"])
        self.assertEqual(workspace.seal(self.run, identifier=sealed["operation_id"])["candidate_id"], sealed["candidate_id"])
        candidate = Path(sealed["candidate_root"])
        self.assertNotEqual((candidate / "tracked").stat().st_ino, (working / "tracked").stat().st_ino)
        (candidate / "tracked").write_bytes(b"hidden change")
        with self.assertRaisesRegex(workspace.WorkspaceError, "candidate changed"):
            workspace.inspect(self.run)


class ScopedWorkflowTests(unittest.TestCase):
    setUp = WorkflowTests.setUp

    def test_phase1_context_observations_ignore_ids_and_times_but_keep_mode_target_bytes(self):
        import workspace_scope
        from copy import deepcopy
        context = self.root / "context"
        context.mkdir()
        (context / "input").write_bytes(b"context bytes")
        alias = self.source / "context-alias"
        alias.symlink_to(context)
        prepared = workspace.prepare(self.run, [self.source], self.destination, context_roots=[context])
        retained = deepcopy(prepared)
        for entry in retained["task_scope"]["context_roots"]:
            entry["identity"] = [-1, -2]
            entry["parent_identity"] = [-3, -4]
        for item in retained["task_scope"]["context_exclusions"]:
            for link in item.get("resolution_chain", []):
                link["descriptor"] = [-1, -2, link["descriptor"][2], 0, 0, 0]
        workspace_scope.verify_context(retained)
        (context / "input").write_bytes(b"different bytes")
        with self.assertRaisesRegex(ValueError, "canonical context changed"):
            workspace_scope.verify_context(retained)

    def test_selected_regular_file_swap_rejected_before_read(self):
        import workspace_scope
        marker = self.root / "outside-marker"
        marker.write_bytes(b"EXTERNAL-MARKER")
        selected = self.root / "selected-file"
        original_read = workspace.read_owned
        for capture in (workspace_scope.context_capture,
                        lambda path: workspace_scope.copy_file(path, None)):
            selected.write_bytes(b"selected bytes")
            reads = []

            def replaced_read(directory, name, **kwargs):
                selected.unlink()
                os.link(marker, selected)
                return original_read(directory, name, **kwargs)

            original_os_read = os.read

            def observed_read(descriptor, size):
                data = original_os_read(descriptor, size)
                reads.append(data)
                return data

            with patch.object(workspace, "read_owned", replaced_read), patch.object(os, "read", observed_read):
                with self.assertRaisesRegex(ValueError, "selected file changed before read"):
                    capture(selected)
            self.assertEqual(reads, [])
            self.assertEqual(marker.read_bytes(), b"EXTERNAL-MARKER")
            selected.unlink()

    def test_prepare_topology_root_swap_rejected_before_external_write(self):
        import workspace_scope
        dependency = self.repository("dependency")
        outside = self.root / "outside-directory"
        outside.mkdir()
        original = workspace_scope.directories
        replaced = []

        def swapped(destination, path, **kwargs):
            if not replaced and destination.name == "baseline":
                retained = destination.with_name("baseline-retained")
                destination.rename(retained)
                outside.rename(destination)
                replaced.append(True)
                try:
                    return original(destination, path, **kwargs)
                finally:
                    destination.rename(outside)
                    retained.rename(destination)
            return original(destination, path, **kwargs)

        with patch.object(workspace_scope, "directories", swapped):
            with self.assertRaisesRegex(ValueError, "registered root identity changed"):
                workspace.prepare(self.run, [self.source, dependency], self.destination)
        self.assertEqual(replaced, [True])
        self.assertEqual(list(outside.iterdir()), [])

    def test_copied_file_destination_parent_swap_rejected_before_write(self):
        import workspace_scope
        source = self.root / "dependency-file"
        source.write_bytes(b"captured bytes")
        destination = self.root / "destination-parent"
        destination.mkdir()
        expected = list(workspace.identity(destination.lstat())[:2])
        outside = self.root / "outside-directory"
        outside.mkdir()
        retained = self.root / "retained-parent"
        original = workspace.write_owned

        def swapped(parent, name, data, **kwargs):
            destination.rename(retained)
            outside.rename(destination)
            try:
                return original(parent, name, data, **kwargs)
            finally:
                destination.rename(outside)
                retained.rename(destination)

        with patch.object(workspace, "write_owned", swapped):
            with self.assertRaisesRegex(ValueError, "registered root identity changed"):
                workspace_scope.copy_file(source, destination / "copied", expected_destination=expected)
        self.assertEqual(list(outside.iterdir()), [])

    def repository(self, name, content=b"dependency"):
        root = self.root / name
        root.mkdir(parents=True)
        workspace.git_command(root, "init", "-q")
        workspace.git_command(root, "config", "user.name", "Fixture")
        workspace.git_command(root, "config", "user.email", "fixture@example.invalid")
        (root / "tracked").write_bytes(content)
        workspace.git_command(root, "add", ".")
        workspace.git_command(root, "commit", "-qm", "fixture baseline")
        return root

    def test_af_ac02_sibling_relative_absolute_links_and_cli(self):
        dependency = self.repository("dependency")
        neighbor = self.root / "unselected"
        neighbor.mkdir()
        (neighbor / "secret").write_bytes(b"excluded neighbor")
        (self.source / "relative").symlink_to("../dependency/tracked")
        (self.source / "absolute").symlink_to(dependency / "tracked")
        cli = Path(workspace.__file__).with_name("task-workspace.py")
        command = [sys.executable, str(cli), "--run-dir", str(self.run), "prepare", "--source", str(self.source),
                   "--source", str(dependency), "--destination", str(self.destination), "--operation-id", "siblings"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = json.loads(result.stdout)
        self.assertEqual(prepared["version"], 2)
        baseline, working = Path(prepared["baseline_root"]), Path(prepared["working_root"])
        self.assertEqual(os.readlink(baseline / "source/absolute"), "../dependency/tracked")
        self.assertEqual((working / "source/relative").read_bytes(), b"dependency")
        self.assertFalse((working / "unselected").exists())
        self.assertEqual(prepared, json.loads(subprocess.run(command, capture_output=True, text=True).stdout))
        sealed = workspace.seal(self.run)
        self.assertEqual(sealed["changed_paths"], [])
        self.assertEqual(sealed["workspace_revision"], prepared["workspace_revision"])
        self.assertEqual(sealed["task_scope_digest"], prepared["task_scope"]["digest"])
        workspace.verify_candidate(workspace.inspect(self.run))
        print("AF-AC02 CLI sibling topology and normalized links passed")

    def test_af_ac02_nested_roots_and_exact_context_alias(self):
        child = self.repository("source/child")
        instructions = self.root / "instructions"
        instructions.mkdir()
        (instructions / "SKILL.md").write_text("Canonical selected context")
        (self.source / "arbitrary-guidance").symlink_to(instructions)
        prepared = workspace.prepare(self.run, [self.source, child], self.destination, context_roots=[instructions])
        scope = prepared["task_scope"]
        self.assertEqual(len(scope["product_roots"]), 2)
        self.assertEqual(scope["context_exclusions"][0]["canonical_path"], str(instructions))
        working = Path(prepared["working_root"])
        self.assertTrue((working / "child/.git").is_dir())
        self.assertFalse((working / "arbitrary-guidance").exists())
        self.assertEqual((working / "child/tracked").read_bytes(), b"dependency")
        workspace.seal(self.run)
        (instructions / "SKILL.md").write_text("changed context")
        with self.assertRaisesRegex(workspace.WorkspaceError, "canonical context changed"):
            workspace.verify_candidate(workspace.registered_workspace(journal_io.JournalSnapshot.open(self.run)))
        revised = workspace.prepare(self.run, [self.source, child], self.destination,
                                    context_roots=[instructions], identifier="context-update")
        self.assertEqual(revised["workspace_revision"], 2)
        self.assertNotIn("seal", workspace.registered_workspace(journal_io.JournalSnapshot.open(self.run)))
        print("AF-AC02 nested roots exact context alias and provenance drift passed")

    def test_af_ac02_product_precedence_and_selected_context_file(self):
        dependency = self.repository("dependency")
        document = self.root / "AGENTS.md"
        document.write_text("selected exact instructions")
        (self.source / "runtime").symlink_to(dependency / "tracked")
        prepared = workspace.prepare(self.run, [self.source, dependency], self.destination,
                                     context_roots=[dependency, document])
        self.assertEqual([entry["canonical_path"] for entry in prepared["task_scope"]["context_roots"]], [str(document)])
        self.assertEqual(prepared["task_scope"]["context_exclusions"], [])
        self.assertEqual((Path(prepared["working_root"]) / "source/runtime").read_bytes(), b"dependency")
        print("AF-AC02 product precedence and exact context file passed")

    def test_af_ac02_expansion_preserves_baseline_changes_and_old_seal(self):
        original = workspace.prepare(self.run, self.source, self.destination)
        Path(original["working_root"], "tracked").write_bytes(b"implementation change")
        previous_seal = workspace.seal(self.run)
        dependency = self.repository("dependency")
        self.source.joinpath("tracked").write_bytes(b"foreign source change")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        old_documents = dict(snapshot.documents)
        expanded = workspace.prepare(self.run, [self.source, dependency], self.destination, identifier="expand")
        self.assertEqual(expanded["workspace_id"], original["workspace_id"])
        self.assertEqual(Path(expanded["baseline_root"], "source/tracked").read_bytes(), b"already dirty")
        self.assertEqual(Path(expanded["working_root"], "source/tracked").read_bytes(), b"implementation change")
        self.assertEqual(Path(expanded["baseline_root"], "dependency/tracked").read_bytes(), b"dependency")
        current = journal_io.JournalSnapshot.open(self.run)
        self.assertTrue(all(current.documents[name] == raw for name, raw in old_documents.items()))
        self.assertNotIn("seal", workspace.registered_workspace(current))
        with self.assertRaisesRegex(workspace.WorkspaceError, "stale workspace revision"):
            workspace.verify_candidate({**expanded, "seal": previous_seal})
        sealed = workspace.seal(self.run)
        self.assertEqual(sealed["changed_paths"], ["source/tracked"])
        self.assertEqual(expanded, workspace.prepare(self.run, [self.source, dependency], self.destination, identifier="expand"))
        print("AF-AC02 expansion retained baseline working changes history and stale seal passed")

    def test_af_ac02_undeclared_targets_nested_repository_and_cycles(self):
        dependency = self.repository("dependency")
        marker = self.root / "outside"
        marker.write_bytes(b"external-secret-must-not-be-read")
        link = self.source / "link"
        for target, error in ((marker, "undeclared symlink target"), ("link", "symlink cycle")):
            with self.subTest(target=target):
                link.symlink_to(target)
                observed = []
                def barrier(phase, context):
                    if phase == "source-read-buffer":
                        observed.append(context["data"])
                with self.assertRaisesRegex(workspace.WorkspaceError, error):
                    workspace.prepare(self.run, [self.source, dependency], self.destination, barrier=barrier)
                self.assertNotIn(marker.read_bytes(), observed)
                link.unlink()
        child = self.repository("source/undeclared")
        with self.assertRaisesRegex(workspace.WorkspaceError, "unsupported nested repository"):
            workspace.prepare(self.run, [self.source, dependency], self.destination)
        self.assertTrue(child.exists())
        self.assertIsNone(workspace.registered_workspace(journal_io.JournalSnapshot.open(self.run)))
        print("AF-AC02 undeclared targets nested repositories and cycles rejected without external reads")

    def test_af_ac02_update_requires_stopped_authors_not_analysis(self):
        from workspace_scope import stopped_workers
        class Snapshot:
            def __init__(self, status, author): self.status, self.author = status, author
            def exists(self, path): return path == "delegation-summary.json"
            def read_bytes(self, path):
                return json.dumps({"verification": {"author_thread_ids": ["author"]}, "subagents": [
                    {"lane_id": "lane", "codex_thread_id": "author" if self.author else "reader", "status": self.status}]}).encode()
        with self.assertRaisesRegex(workspace.WorkspaceError, "stop current product authors"):
            stopped_workers(Snapshot("active", True))
        stopped_workers(Snapshot("pass", True))
        stopped_workers(Snapshot("active", False))
        print("AF-AC02 active author update gate and read-only assignment allowance passed")

    def test_af_ac02_ordinary_directory_and_exact_file_dependencies(self):
        directory = self.root / "assets"
        directory.mkdir()
        (directory / "icon.svg").write_text("<svg/>")
        file = self.root / "settings.json"
        file.write_text('{"fixture": true}')
        file.chmod(0o640)
        (self.source / "asset").symlink_to(directory / "icon.svg")
        (self.source / "settings").symlink_to(file)
        original = workspace.prepare(self.run, [self.source, directory, file], self.destination)
        working = Path(original["working_root"])
        self.assertEqual((working / "source/asset").read_text(), "<svg/>")
        self.assertEqual((working / "source/settings").read_text(), '{"fixture": true}')
        self.assertFalse((working / "assets/.git").exists())
        self.assertEqual((working / "settings.json").stat().st_mode & 0o777, 0o640)
        (working / "settings.json").write_text("edited settings")
        another = self.root / "extra.txt"
        another.write_text("new dependency")
        updated = workspace.prepare(self.run, [self.source, directory, file, another], self.destination, identifier="file-expand")
        self.assertEqual(Path(updated["baseline_root"], "settings.json").read_text(), '{"fixture": true}')
        self.assertEqual(Path(updated["working_root"], "settings.json").read_text(), "edited settings")
        self.assertEqual(workspace.seal(self.run)["changed_paths"], ["settings.json"])
        print("AF-AC02 ordinary directory and exact file dependencies retained across expansion passed")

    def test_af_ac02_nested_expansion_uses_retained_content(self):
        child = self.source / "child"
        child.mkdir()
        (child / "data").write_text("original child")
        original = workspace.prepare(self.run, self.source, self.destination)
        Path(original["working_root"], "child/data").write_text("child implementation")
        workspace.git_command(child, "init", "-q")
        (child / "data").write_text("foreign child source")
        updated = workspace.prepare(self.run, [self.source, child], self.destination, identifier="nested-expand")
        self.assertEqual(Path(updated["baseline_root"], "child/data").read_text(), "original child")
        self.assertEqual(Path(updated["working_root"], "child/data").read_text(), "child implementation")
        self.assertTrue(Path(updated["working_root"], "child/.git").is_dir())
        self.assertEqual(workspace.seal(self.run)["changed_paths"], ["child/data"])
        print("AF-AC02 nested expansion preserved earlier baseline and working edits passed")

    def test_af_ac02_new_parent_topology_preserves_old_root(self):
        original = workspace.prepare(self.run, self.source, self.destination)
        Path(original["working_root"], "tracked").write_text("retained implementation")
        workspace.git_command(self.root, "init", "-q")
        (self.root / "new-parent-file").write_text("parent addition")
        updated = workspace.prepare(self.run, [self.source, self.root], self.destination, identifier="parent-expand")
        self.assertEqual(Path(updated["baseline_root"], "source/tracked").read_bytes(), b"already dirty")
        self.assertEqual(Path(updated["working_root"], "source/tracked").read_text(), "retained implementation")
        self.assertEqual(Path(updated["working_root"], "new-parent-file").read_text(), "parent addition")
        self.assertEqual(workspace.seal(self.run)["changed_paths"], ["source/tracked"])
        print("AF-AC02 parent expansion retained old root and excluded journal bundle passed")

    def test_af_ac02_context_parent_product_file_precedence_and_tamper(self):
        selected = self.root / "references"
        selected.mkdir()
        document = selected / "GUIDANCE.md"
        document.write_text("instructions")
        asset = selected / "asset.dat"
        asset.write_bytes(b"runtime bytes")
        (self.source / "alias").symlink_to(document)
        (self.source / "asset").symlink_to(asset)
        prepared = workspace.prepare(self.run, [self.source, asset], self.destination, context_roots=[selected])
        self.assertEqual(prepared["task_scope"]["context_roots"][0]["manifest"]["exclusions"], ["asset.dat"])
        working = Path(prepared["working_root"])
        (working / "source/alias").write_text("tamper at contextual alias")
        with self.assertRaisesRegex(workspace.WorkspaceError, "context alias must be absent"):
            workspace.seal(self.run)
        (working / "source/alias").unlink()
        (working / "outside-product-root").write_text("undeclared edit")
        with self.assertRaisesRegex(workspace.WorkspaceError, "outside selected product roots"):
            workspace.seal(self.run)
        print("AF-AC02 contextual alias tamper and anchor-only product edits rejected")

    def test_af_ac02_indirect_product_link_to_context_and_escape(self):
        dependency = self.repository("dependency")
        document = self.root / "GUIDANCE.md"
        document.write_text("instructions")
        (self.source / "context-alias").symlink_to(document)
        link = self.source / "indirect"
        link.symlink_to("context-alias")
        with self.assertRaisesRegex(workspace.WorkspaceError, "uses excluded context alias"):
            workspace.prepare(self.run, [self.source, dependency], self.destination, context_roots=[document])
        link.unlink()
        (self.source / "context-alias").unlink()
        (self.source / "base").symlink_to(".")
        link.symlink_to("base/..")
        with self.assertRaisesRegex(workspace.WorkspaceError, "leaves selected roots"):
            workspace.prepare(self.run, [self.source, dependency], self.destination)
        print("AF-AC02 indirect context dependency and indirect boundary escape rejected")

    def test_af_ac02_v2_source_destination_swap_no_external_writes(self):
        dependency = self.repository("dependency")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "marker").write_bytes(b"external-marker")
        changed = False
        def barrier(phase, context):
            nonlocal changed
            if phase == "prepare-before-copy" and not changed:
                changed = True
                attempt = Path(context["attempt"])
                attempt.rename(attempt.with_name(attempt.name + "-retained"))
                attempt.symlink_to(outside)
        with self.assertRaises(workspace.WorkspaceError):
            workspace.prepare(self.run, [self.source, dependency], self.destination, barrier=barrier)
        self.assertEqual(set(path.name for path in outside.iterdir()), {"marker"})
        self.assertEqual((outside / "marker").read_bytes(), b"external-marker")
        self.assertIsNone(workspace.registered_workspace(journal_io.JournalSnapshot.open(self.run)))
        print("AF-AC02 v2 destination swap rejected without external writes")

    def test_af_ac02_declared_initialized_gitlink_only(self):
        child = self.repository("source/module")
        commit = workspace.git_command(child, "rev-parse", "HEAD").stdout.decode().strip()
        workspace.git_command(self.source, "update-index", "--add", "--cacheinfo", "160000," + commit + ",module")
        prepared = workspace.prepare(self.run, [self.source, child], self.destination)
        self.assertEqual(Path(prepared["working_root"], "module/tracked").read_bytes(), b"dependency")
        self.assertTrue(Path(prepared["working_root"], "module/.git").is_dir())
        self.assertEqual(workspace.seal(self.run)["changed_paths"], [])
        print("AF-AC02 explicitly declared initialized gitlink captured independently passed")

    def test_af_ac02_context_alias_becomes_product_on_expansion(self):
        dependency = self.root / "context-or-runtime"
        dependency.mkdir()
        (dependency / "material").write_text("selected material")
        (self.source / "alias").symlink_to(dependency)
        first = workspace.prepare(self.run, self.source, self.destination, context_roots=[dependency])
        self.assertFalse(Path(first["working_root"], "alias").exists())
        updated = workspace.prepare(self.run, [self.source, dependency], self.destination,
                                    context_roots=[dependency], identifier="promote-dependency")
        self.assertEqual(updated["task_scope"]["context_exclusions"], [])
        self.assertEqual(Path(updated["working_root"], "source/alias/material").read_text(), "selected material")
        self.assertEqual(workspace.seal(self.run)["changed_paths"], [])
        print("AF-AC02 expanded product precedence restores former contextual alias passed")

    def test_af_ac02_old_operation_retry_and_frozen_identity(self):
        from dataclasses import replace
        first = workspace.prepare(self.run, self.source, self.destination, identifier="initial-single")
        dependency = self.repository("dependency")
        updated = workspace.prepare(self.run, [self.source, dependency], self.destination, identifier="expand")
        self.assertEqual(workspace.prepare(self.run, self.source, self.destination, identifier="initial-single"), first)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual(workspace.registered_workspace(snapshot), updated)
        extra = self.root / "new-file"
        extra.write_text("additional dependency")
        with patch.object(journal_io.JournalSnapshot, "open", return_value=replace(snapshot, archives={"frozen": b"snapshot"})):
            with self.assertRaisesRegex(workspace.WorkspaceError, "identity is frozen"):
                workspace.prepare(self.run, [self.source, dependency, extra], self.destination, identifier="frozen-expand")
        self.assertEqual(journal_io.JournalSnapshot.open(self.run).revision, snapshot.revision)
        print("AF-AC02 old technical retry preserves current receipt and frozen expansion rejected")

    def test_af_ac02_chained_canonical_context_and_chain_tamper(self):
        canonical = self.root / "canonical-skill"
        canonical.mkdir()
        (canonical / "SKILL.md").write_text("selected canonical instructions")
        installed = self.root / "installed-skill"
        installed.symlink_to(canonical)
        (self.source / "skill-alias").symlink_to(installed / "SKILL.md")
        prepared = workspace.prepare(self.run, self.source, self.destination, context_roots=[canonical])
        alias = prepared["task_scope"]["context_exclusions"][0]
        self.assertEqual(alias["canonical_path"], str(canonical / "SKILL.md"))
        self.assertEqual(alias["resolution_chain"][0]["source"], str(installed))
        self.assertFalse(Path(prepared["working_root"], "skill-alias").exists())
        workspace.seal(self.run)
        external = self.root / "external-skill"
        external.mkdir()
        (external / "SKILL.md").write_text("unknown external instructions")
        installed.unlink()
        installed.symlink_to(external)
        with self.assertRaisesRegex(workspace.WorkspaceError, "context link chain changed"):
            workspace.verify_candidate(workspace.registered_workspace(journal_io.JournalSnapshot.open(self.run)))
        print("AF-AC02 canonical chained context alias captured and mutated chain rejected")

    def test_af_ac02_context_chain_cycle_and_unknown_final_target(self):
        canonical = self.root / "canonical-skill"
        canonical.mkdir()
        (canonical / "SKILL.md").write_text("selected context")
        external = self.root / "outside-alias"
        external.symlink_to("outside-alias")
        (self.source / "alias").symlink_to(external)
        with self.assertRaisesRegex(workspace.WorkspaceError, "context symlink cycle"):
            workspace.prepare(self.run, self.source, self.destination, context_roots=[canonical])
        external.unlink()
        marker = self.root / "unknown-file"
        marker.write_bytes(b"unknown-secret")
        external.symlink_to(marker)
        with patch.object(workspace, "read_owned", wraps=workspace.read_owned) as reads:
            with self.assertRaisesRegex(workspace.WorkspaceError, "undeclared symlink target"):
                workspace.prepare(self.run, self.source, self.destination, context_roots=[canonical])
            self.assertFalse(any(call.args[0] == marker.parent and call.args[1] == marker.name for call in reads.call_args_list))
        print("AF-AC02 context chain cycle and unknown final target rejected without content read")

    def test_af_ac02_absolute_product_link_preserves_symlink_before_parent(self):
        dependency = self.repository("dependency", b"semantic target")
        (dependency / "subdir").mkdir()
        (self.source / "tracked").write_text("lexical target differs")
        (self.source / "jump").symlink_to("../dependency/subdir")
        target = str(self.source / "jump/../tracked")
        (self.source / "absolute").symlink_to(target)
        self.assertEqual((self.source / "absolute").read_bytes(), b"semantic target")
        prepared = workspace.prepare(self.run, [self.source, dependency], self.destination)
        self.assertEqual(Path(prepared["working_root"], "source/absolute").read_bytes(), b"semantic target")
        self.assertEqual(os.readlink(Path(prepared["baseline_root"], "source/absolute")), "jump/../tracked")
        self.assertEqual(workspace.seal(self.run)["changed_paths"], [])
        print("AF-AC02 absolute product symlink-before-parent semantics preserved")

    def test_af_ac02_absolute_ordered_target_reenters_declared_root(self):
        dependency = self.repository("dependency", b"semantic target")
        (dependency / "deep/more").mkdir(parents=True)
        (self.source / "jump").symlink_to("../dependency/deep/more")
        (self.source / "absolute").symlink_to(str(self.source / "jump/../../tracked"))
        prepared = workspace.prepare(self.run, [self.source, dependency], self.destination)
        self.assertEqual(Path(prepared["working_root"], "source/absolute").read_bytes(), b"semantic target")
        self.assertEqual(workspace.seal(self.run)["changed_paths"], [])
        print("AF-AC02 ordered absolute link resolves into declared root despite lexical escape")

    def test_af_ac02_context_parent_component_order_and_revision_recovery(self):
        declared = self.root / "declared-context"
        declared.mkdir()
        (declared / "subdir").mkdir()
        (declared / "SKILL.md").write_text("semantic instructions")
        installed = self.root / "installed"
        installed.mkdir()
        (installed / "SKILL.md").write_text("lexical instructions differ")
        (installed / "jump").symlink_to(declared / "subdir")
        (self.source / "alias").symlink_to(installed / "jump/../SKILL.md")
        prepared = workspace.prepare(self.run, self.source, self.destination, context_roots=[declared])
        alias = prepared["task_scope"]["context_exclusions"][0]
        self.assertEqual(alias["canonical_path"], str(declared / "SKILL.md"))
        (installed / "jump").unlink()
        (installed / "jump").symlink_to(declared / "subdir")
        workspace.seal(self.run)
        updated = workspace.prepare(self.run, self.source, self.destination, context_roots=[declared], identifier="refresh-chain")
        self.assertEqual(updated["workspace_revision"], prepared["workspace_revision"] + 1)
        self.assertNotEqual(updated["task_scope"]["digest"], prepared["task_scope"]["digest"])
        self.assertEqual(workspace.seal(self.run)["changed_paths"], [])
        print("AF-AC02 context symlink-before-parent order and fresh revision recovery passed")


if __name__ == "__main__":
    unittest.main()
