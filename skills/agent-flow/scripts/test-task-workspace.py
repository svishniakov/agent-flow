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


if __name__ == "__main__":
    unittest.main()
