#!/usr/bin/env python3
"""Fixture tests for lane boundary capture inside the combined author conclusion."""

from __future__ import annotations

import json
import subprocess
import sys
import importlib.util
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import journal_io
from journal_io import JournalError, JournalSnapshot, initialize_journal, transact
from verification_evidence import sha256, result_hash, empty_verification, author_coverage, resolve_author_boundaries, EvidenceError
from task_workspace import prepare, seal, registered_workspace


ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "scripts" / "record-agent-trace.py"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def conclude(run_dir, lane_id, artifacts, authored_paths, *, operation=None, summary=None, coauthors=()):
    """Combined author conclusion: handoff registration and boundary capture in one call."""
    command = [sys.executable, "-B", str(RECORDER), "--run-dir", str(run_dir), "--prepare-conclusion",
               "--role", "python-worker", "--lane-id", lane_id, "--status", "pass"]
    if summary is not None:
        command += ["--summary", summary]
    for artifact in artifacts:
        command += ["--artifact", artifact]
    for path in authored_paths:
        command += ["--authored-path", path]
    for coauthor in coauthors:
        command += ["--coauthor", coauthor]
    if operation:
        command += ["--operation-id", operation]
    return subprocess.run(command, text=True, capture_output=True)


def native_author_fixtures():
    validator = load("boundary_validator", "validate-run.py")
    with tempfile.TemporaryDirectory(prefix="impl018-authors-") as temporary:
        root = Path(temporary).resolve(); repo = root / "product"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "a").write_text("baseline a\n"); (repo / "b").write_text("baseline b\n")
        run_dir = root / "run"
        records = [{"lane_id": lane, "role": "python-worker", "codex_thread_id": f"00000000-0000-4000-8000-{index:012d}",
            "trace": f"agents/{lane}/trace.jsonl"} for index, lane in enumerate(("author-a", "author-b"), 11)]
        summary = {"version": 1, "subagents_used": True, "role_lanes_used": False, "notes": "Own contribution fixtures",
            "subagents": records, "role_lanes": [], "verification": empty_verification()}
        root_id = "00000000-0000-4000-8000-000000000099"
        root_record = {"lane_id": "root-author", "role": "python-worker", "trace": "agents/root-author/trace.jsonl"}
        summary["role_lanes"] = [root_record]; summary["role_lanes_used"] = True
        summary["verification"]["root_thread_id"] = root_id
        lanes = [{"id": record["lane_id"], "type": "implementation", "status": "pass", "wave": 1, "boundary": {"allowed_paths": [path], "forbidden_paths": [],
            "changed_paths_artifact": f"checks/lane-boundary-{record['lane_id']}.json", "notes": "Own paths"},
            "evidence": [f"checks/lane-boundary-{record['lane_id']}.json"]} for record, path in zip(records, ("a", "b"))]
        documents = {"delegation-summary.json": json.dumps(summary), "lane-map.json": json.dumps({"schema_version": 2, "architecture_contract_required": False, "lanes": lanes}), "final.md": "Verdict: ship\n",
            "context.md": "## Initial Worktree Snapshot\nBaseline\n", "plan.md": "Own local edits\n"}
        for record in records:
            event = {"lane_id": record["lane_id"], "role": record["role"], "codex_thread_id": record["codex_thread_id"], "execution_mode": "subagent"}
            documents[record["trace"]] = json.dumps(event) + "\n"
        documents[root_record["trace"]] = json.dumps({"lane_id": "root-author", "role": "python-worker", "execution_mode": "role-lane"}) + "\n"
        initialize_journal(run_dir, {key: value.encode() for key, value in documents.items()}, source_root=repo)
        prepared = prepare(run_dir, repo, root / "bundle")
        for path in ("a", "b"):
            (Path(prepared["working_root"]) / path).write_text("owned new " + path + "\n")
        sealed = seal(run_dir)
        for lane, path in zip(lanes, ("a", "b")):
            lane_id = lane["id"]; handoff = f"handoffs/{lane_id}.md"; proof = f"checks/{lane_id}-edit.patch"
            handoff_bytes = f"Own authored path: {path}\n".encode()
            proof_bytes = f"--- a/{path}\n+++ b/{path}\n-owned baseline\n+owned new {path}\n".encode()
            transact(run_dir, "declare-" + lane_id, {}, lambda current: ({handoff: handoff_bytes, proof: proof_bytes}, {}))
            result = conclude(run_dir, lane_id, [handoff, proof], [path], operation="boundary-" + lane_id)
            assert result.returncode == 0, result.stderr
            answer = json.loads(result.stdout)
            assert answer["boundary"] == f"checks/lane-boundary-{lane_id}.json"
            assert answer["reviewed_result_hash"] is None
            snapshot = JournalSnapshot.open(run_dir)
            artifact = json.loads(snapshot.read_text(lane["boundary"]["changed_paths_artifact"]))
            assert artifact["changed_paths"] == sealed["changed_paths"] and artifact["authored_paths"] == [path]
            assert validator.validate_lane_boundary(run_dir, lane, lane_id, required=True, allow_pending=False, snapshot=snapshot)[1] == []
            forbidden = {**lane, "boundary": {**lane["boundary"], "forbidden_paths": [path]}}
            assert any("forbidden_paths" in error for error in validator.validate_lane_boundary(run_dir, forbidden, lane_id, required=True, allow_pending=False, snapshot=snapshot)[1])
            # A declaration outside the own seal delta is rejected before any write.
            rejected = conclude(run_dir, lane_id, [handoff, proof], ["outside-delta"])
            assert rejected.returncode != 0 and "outside workspace delta" in rejected.stderr, rejected.stderr
        # Coauthor disclosure must name a registered assignment.
        unknown = conclude(run_dir, "author-a", ["handoffs/author-a.md", "checks/author-a-edit.patch"], ["a"],
                           coauthors=["unknown-author:a"])
        assert unknown.returncode != 0 and "unknown coauthor" in unknown.stderr, unknown.stderr
        snapshot = JournalSnapshot.open(run_dir)
        declarations = [json.loads(snapshot.read_text(lane["boundary"]["changed_paths_artifact"])) for lane in lanes]
        assert set().union(*(set(d["authored_paths"]) for d in declarations)) == set(sealed["changed_paths"])
        def author_errors():
            return [error for error in validator.validate_lane_map(run_dir, snapshot=JournalSnapshot.open(run_dir)) if error.startswith("author ") or "boundary version 2" in error]
        assert author_errors() == [], author_errors()
        first_path = lanes[0]["boundary"]["changed_paths_artifact"]
        original = declarations[0]
        for operation, replacement, marker in [
            ("uncovered", {**original, "authored_paths": []}, "union must equal"),
            ("fresh-v1", {key: value for key, value in original.items() if key not in {"authored_paths", "handoff", "edit_evidence", "coauthors", "assignment"}} | {"version": 1}, "boundary version 2")]:
            transact(run_dir, operation, {}, lambda current: ({first_path: json.dumps(replacement)}, {}))
            assert any(marker in error for error in author_errors()), (operation, author_errors())
        # Forged reference hashes are caught by the writer-side reference check.
        forged = {"lane_id": "author-a", "authored_paths": ["a"], "handoff": {"path": "handoffs/author-a.md", "sha256": "0" * 64},
                  "edit_evidence": [{"path": "checks/author-a-edit.patch", "sha256": sha256(JournalSnapshot.open(run_dir).read_bytes("checks/author-a-edit.patch"))}], "coauthors": {}}
        try:
            journal_io.author_boundary_artifact(JournalSnapshot.open(run_dir), "author-a", forged)
        except JournalError as exc:
            assert "sha256 mismatch" in str(exc), str(exc)
        else:
            raise AssertionError("forged handoff hash accepted")
        # Registered contributions derive shared authors; disclosure is optional.
        overlap_a = {**original, "authored_paths": ["a", "b"], "coauthors": {"b": ["author-b"]}}
        overlap_b = {**declarations[1], "coauthors": {"b": ["author-a"]}}
        shared_documents = {}
        for declaration, peer in ((overlap_a, "author-b"), (overlap_b, "author-a")):
            handoff = ("Own paths " + " ".join(declaration["authored_paths"]) + " shared b with " + peer + "\n").encode()
            proof = ("--- a/b\n+++ b/b\n+shared edit by " + declaration["lane_id"] + "\n").encode()
            declaration["handoff"] = {**declaration["handoff"], "sha256": sha256(handoff)}
            declaration["edit_evidence"] = [{**declaration["edit_evidence"][0], "sha256": sha256(proof)}]
            shared_documents[declaration["handoff"]["path"]] = handoff
            shared_documents[declaration["edit_evidence"][0]["path"]] = proof
        lanes[0]["boundary"]["allowed_paths"] = ["a", "b"]
        shared_documents.update({first_path: json.dumps(overlap_a), lanes[1]["boundary"]["changed_paths_artifact"]: json.dumps(overlap_b),
            "lane-map.json": json.dumps({"schema_version": 2, "architecture_contract_required": False, "lanes": lanes})})
        transact(run_dir, "mutual-coauthors", {}, lambda current: (shared_documents, {}))
        assert author_errors() == [], author_errors()
        for lane in lanes:
            assert validator.validate_lane_boundary(run_dir, lane, lane["id"], required=True, allow_pending=False, snapshot=JournalSnapshot.open(run_dir))[1] == []
        overlap_b["coauthors"]["b"].append("unknown-author")
        transact(run_dir, "extra-coauthor", {}, lambda current: ({lanes[1]["boundary"]["changed_paths_artifact"]: json.dumps(overlap_b)}, {}))
        assert any("coauthorship mismatch" in error for error in author_errors())
        overlap_b["coauthors"]["b"].remove("unknown-author")
        transact(run_dir, "restore-known-coauthor", {}, lambda current: ({lanes[1]["boundary"]["changed_paths_artifact"]: json.dumps(overlap_b)}, {}))
        root_handoff = b"Root authored a\n"
        root_proof = b"--- a/a\n+++ b/a\n+root own edit\n"
        transact(run_dir, "declare-root", {}, lambda current: ({"handoffs/root-author.md": root_handoff, "checks/root-edit.patch": root_proof}, {}))
        result = conclude(run_dir, "root-author", ["handoffs/root-author.md", "checks/root-edit.patch"], ["a"], operation="boundary-root")
        assert result.returncode == 0, result.stderr
        assert json.loads(JournalSnapshot.open(run_dir).read_text("checks/lane-boundary-root-author.json"))["assignment"]["codex_thread_id"] == root_id
        print("PASS impl018 native boundary v2 disjoint author permissions full delta forbidden and forged handoff controls")


def correction_state_fixtures():
    """Synthetic author records; actual retained trees and native combined conclusion CLI."""
    with tempfile.TemporaryDirectory(prefix="author-correction-") as temporary:
        root = Path(temporary).resolve(); source = root / "source"; source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "existing").write_text("baseline\n")
        (source / "existing-dir").mkdir()
        records = [{"lane_id": lane, "role": "python-worker", "codex_thread_id": f"00000000-0000-4000-8000-{index:012d}",
                    "trace": f"agents/{lane}/trace.jsonl"} for index, lane in enumerate(("first", "fix", "cancel"), 21)]
        summary = {"version": 1, "subagents_used": True, "role_lanes_used": False, "notes": "Author correction fixtures",
                   "subagents": records, "role_lanes": [], "verification": empty_verification()}
        documents = {"delegation-summary.json": json.dumps(summary).encode()}
        for record in records:
            documents[record["trace"]] = (json.dumps({**record, "execution_mode": "subagent", "status": "pass"}) + "\n").encode()
        run_dir = root / "run"
        initialize_journal(run_dir, documents, source_root=source)
        prepared = prepare(run_dir, source, root / "bundle")
        working = Path(prepared["working_root"])
        nested = working / "tests/browser/server/result"
        nested.parent.mkdir(parents=True); nested.write_text("first\n")
        (working / "existing").write_text("first\n")
        def record(lane, paths):
            handoff = ("Own paths: " + " ".join(paths) + "\n").encode()
            proof = json.dumps({"written_paths": paths, "actor": lane}).encode()
            transact(run_dir, "declare-" + lane, {}, lambda current: ({f"handoffs/{lane}.md": handoff, f"checks/{lane}-edit.json": proof}, {}))
            result = conclude(run_dir, lane, [f"handoffs/{lane}.md", f"checks/{lane}-edit.json"], paths)
            assert result.returncode == 0, result.stderr
        def coverage():
            snapshot = JournalSnapshot.open(run_dir); workspace = registered_workspace(snapshot)
            author_coverage(snapshot, workspace, resolve_author_boundaries(snapshot, workspace))
        def rejected(marker):
            try:
                coverage()
            except EvidenceError as exc:
                assert marker in str(exc), str(exc)
            else:
                raise AssertionError("unowned state accepted: " + marker)
        seal(run_dir); record("first", ["existing", "tests/browser/server/result"]); coverage()
        snapshot = JournalSnapshot.open(run_dir); workspace = registered_workspace(snapshot)
        entries = resolve_author_boundaries(snapshot, workspace)
        with_forbidden_parent = [{**entry, "boundary": {"forbidden_paths": ["tests/browser"]}} for entry in entries]
        try:
            author_coverage(snapshot, workspace, with_forbidden_parent)
        except EvidenceError as exc:
            assert "forbidden_paths" in str(exc), str(exc)
        else:
            raise AssertionError("forbidden parent directory accepted")
        first_snapshot = JournalSnapshot.open(run_dir)
        originals = {name: first_snapshot.read_bytes(name) for name in ("handoffs/first.md", "checks/lane-boundary-first.json")}
        nested.write_text("unregistered\n"); seal(run_dir)
        rejected("current state differs")
        replay = conclude(run_dir, "first", ["handoffs/first.md", "checks/first-edit.json"], ["existing", "tests/browser/server/result"])
        assert replay.returncode != 0 and "immutable" in replay.stderr, replay.stderr
        record("fix", ["tests/browser/server/result"]); coverage()
        # A subsequent directory chmod is independent work, even with owned files.
        nested.parent.chmod(0o700); seal(run_dir); rejected("union must equal")
        nested.parent.chmod(0o755); seal(run_dir); coverage()
        empty = working / "empty"; empty.mkdir(); seal(run_dir); rejected("union must equal")
        empty.rmdir(); seal(run_dir); coverage()
        replaced = working / "existing-dir"
        replaced.rmdir(); replaced.symlink_to("existing"); seal(run_dir); rejected("union must equal")
        replaced.unlink(); replaced.mkdir(); seal(run_dir); coverage()
        # Unrecorded restore cannot erase an earlier contribution from history.
        (working / "existing").write_text("baseline\n"); seal(run_dir); rejected("current state differs")
        nested.unlink()
        for path in (nested.parent, nested.parent.parent, working / "tests"):
            path.rmdir()
        seal(run_dir); record("cancel", ["existing", "tests/browser/server/result"]); coverage()
        final = JournalSnapshot.open(run_dir)
        assert originals == {name: final.read_bytes(name) for name in originals}
        print("PASS sequential corrections, immutable proof, inferred parents, unowned bytes, chmod/empty directories and cancelled contributions")


def concurrent_boundary_fixture():
    """Interleave stale preflight reads while using the real transactional writer."""
    recorder = load("boundary_conclusion_recorder", "record-agent-trace.py")
    with tempfile.TemporaryDirectory(prefix="author-boundary-race-") as temporary:
        root = Path(temporary).resolve(); source = root / "source"; source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        for path in ("a", "b"):
            (source / path).write_text("baseline\n")
        assignment = {"lane_id": "author", "role": "python-worker", "status": "pass",
                      "codex_thread_id": "00000000-0000-4000-8000-000000000021",
                      "trace": "agents/author/trace.jsonl", "handoff": "handoffs/author.md"}
        handoff = b"Done.\n"; proof = b"Actual edit a b\n"
        run_dir = root / "run"
        initialize_journal(run_dir, {
            "delegation-summary.json": json.dumps({"version": 1, "subagents_used": True, "role_lanes_used": False,
                                                   "notes": "Concurrent boundary fixture",
                                                   "subagents": [assignment], "role_lanes": [],
                                                   "verification": empty_verification()}).encode(),
            assignment["trace"]: (json.dumps({**assignment, "execution_mode": "subagent"}) + "\n").encode(),
            assignment["handoff"]: handoff, "artifacts/edit.txt": proof}, source_root=source)
        prepared = prepare(run_dir, source, root / "bundle")
        for path in ("a", "b"):
            (Path(prepared["working_root"]) / path).write_text("changed\n")
        seal(run_dir)
        declaration = {"lane_id": "author", "authored_paths": ["a"],
                       "handoff": {"path": assignment["handoff"], "sha256": sha256(handoff)},
                       "edit_evidence": [{"path": "artifacts/edit.txt", "sha256": sha256(proof)}], "coauthors": {}}
        from verification_evidence import verify_author_declaration
        try:
            verify_author_declaration(JournalSnapshot.open(run_dir), declaration, lane_id="author")
        except EvidenceError as exc:
            assert "handoff must confirm authored_paths" in str(exc), str(exc)
        else:
            raise AssertionError("full author audit accepted shortened prose")

        def conclusion(operation, paths):
            args = SimpleNamespace(sections_file=None, summary=None, status="pass", lane_id="author",
                                   role="python-worker", reason=None, unverified=[], context_classification_json=None,
                                   readiness_gates_file=None, authored_path=list(paths), coauthor=[], operation_id=operation)
            return recorder.prepare_conclusion(run_dir, args, ["handoffs/author.md", "artifacts/edit.txt"],
                                               recorder.CodexSessionSource())

        original_write = journal_io.write_entry
        saved = {}
        def interleave(*writer_args, **writer_kwargs):
            journal_io.write_entry = original_write
            conclusion("concurrent-first", ["a"])
            saved["boundary"] = JournalSnapshot.open(run_dir).read_bytes("checks/lane-boundary-author.json")
            return original_write(*writer_args, **writer_kwargs)
        journal_io.write_entry = interleave
        try:
            conclusion("concurrent-second", ["a", "b"])
        except JournalError as exc:
            assert "immutable" in str(exc), str(exc)
        else:
            raise AssertionError("concurrent publication overwrote immutable author contribution")
        finally:
            journal_io.write_entry = original_write
        snapshot = JournalSnapshot.open(run_dir)
        assert snapshot.read_bytes("checks/lane-boundary-author.json") == saved["boundary"]
        assert snapshot.read_bytes(assignment["handoff"]) == handoff
        assert snapshot.operation_receipt("concurrent-second") is None
        conclusion("concurrent-first", ["a"])
        conclusion("exact-replay", ["a"])
        assert JournalSnapshot.open(run_dir).read_bytes("checks/lane-boundary-author.json") == saved["boundary"]
        print("PASS concurrent author publication rejects overwrite and preserves exact replay")


def combined_conclusion_fixtures():
    """Combined author conclusion: generated handoff, registration and boundary in one transaction."""
    base = load("boundary_base_fixtures", "test-verification-evidence.py")
    recorder = load("combined_conclusion_recorder", "record-agent-trace.py")
    with tempfile.TemporaryDirectory(prefix="author-combined-") as temporary:
        root = Path(temporary).resolve(); source_dir = root / "source"; source_dir.mkdir()
        subprocess.run(["git", "init", "-q", str(source_dir)], check=True)
        (source_dir / "owned.txt").write_text("baseline owned\n")
        (source_dir / "lib.py").write_text("baseline lib\n")
        run_dir = root / "run"
        summary = {"version": 1, "subagents_used": False, "role_lanes_used": False, "notes": "Combined conclusion fixture",
                   "subagents": [], "role_lanes": [], "verification": {**empty_verification(), "root_thread_id": base.ROOT_ID}}
        initialize_journal(run_dir, {"delegation-summary.json": json.dumps(summary).encode()}, source_root=source_dir)
        source = base.SyntheticSource()
        source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
        thread = "00000000-0000-4000-8000-000000000031"
        source.sessions[thread] = base.session(thread, "python-worker", {}, 3)
        prepared = prepare(run_dir, source_dir, root / "bundle")
        working = Path(prepared["working_root"])

        def record(arguments, operation):
            with redirect_stdout(StringIO()) as output:
                code = recorder.main(["--run-dir", str(run_dir), *arguments, "--operation-id", operation], session_source=source)
            assert code == 0, operation
            return output.getvalue()

        verification = {**empty_verification(), "task_kind": "change", "root_thread_id": base.ROOT_ID}
        record(["--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification",
                "--status", "active", "--summary", "Scope registered", "--verification-json", json.dumps(verification)], "scope")
        record(["--role", "python-worker", "--execution-mode", "role-lane", "--lane-id", "root-author",
                "--stage", "spawned", "--status", "active", "--summary", "Assigned owned.txt"], "spawn-root")
        record(["--role", "python-worker", "--lane-id", "sub-author",
                "--stage", "spawned", "--status", "active", "--codex-thread-id", thread, "--summary", "Assigned lib.py"], "spawn-sub")
        (working / "owned.txt").write_text("owned new\n")
        (working / "lib.py").write_text("owned lib\n")
        sealed = seal(run_dir)
        assert set(sealed["changed_paths"]) == {"owned.txt", "lib.py"}
        proofs = {"root-author": json.dumps({"written_paths": ["owned.txt"]}).encode(),
                  "sub-author": json.dumps({"written_paths": ["lib.py"]}).encode()}
        transact(run_dir, "author-proofs", {}, lambda current: (
            {f"checks/{lane}-edit.json": proof for lane, proof in proofs.items()}, {}))

        # Role-lane author: handoff registration and boundary capture commit together.
        answer = json.loads(record(["--prepare-conclusion", "--role", "python-worker", "--lane-id", "root-author",
            "--status", "pass", "--summary", "Own authored path: owned.txt",
            "--artifact", "handoffs/root-author.md", "--artifact", "checks/root-author-edit.json",
            "--authored-path", "owned.txt"], "combined-root"))
        snapshot = JournalSnapshot.open(run_dir)
        assert answer["boundary"] == "checks/lane-boundary-root-author.json"
        assert answer["reviewed_result_hash"] is None
        artifact = json.loads(snapshot.read_text(answer["boundary"]))
        assert artifact["version"] == 2 and artifact["authored_paths"] == ["owned.txt"]
        assert artifact["assignment"]["codex_thread_id"] == base.ROOT_ID
        handoff_text = snapshot.read_text("handoffs/root-author.md")
        assert "owned.txt" in handoff_text and "checks/root-author-edit.json" in handoff_text
        stored = json.loads(snapshot.read_text("delegation-summary.json"))
        registered = next(row for row in stored["role_lanes"] if row["lane_id"] == "root-author")
        assert (registered.get("status"), registered.get("handoff")) == ("pass", "handoffs/root-author.md")
        assert any(event.get("lane_id") == "root-author" and event.get("stage") == "handoff"
                   for event in (json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()))
        revision = snapshot.revision
        # Exact retry replays the committed conclusion.
        record(["--prepare-conclusion", "--role", "python-worker", "--lane-id", "root-author",
            "--status", "pass", "--summary", "Own authored path: owned.txt",
            "--artifact", "handoffs/root-author.md", "--artifact", "checks/root-author-edit.json",
            "--authored-path", "owned.txt"], "combined-root")
        assert JournalSnapshot.open(run_dir).revision == revision
        # A changed authored set under the same operation ID is a payload conflict.
        try:
            record(["--prepare-conclusion", "--role", "python-worker", "--lane-id", "root-author",
                "--status", "pass", "--summary", "Own authored path: owned.txt",
                "--artifact", "handoffs/root-author.md", "--artifact", "checks/root-author-edit.json",
                "--authored-path", "lib.py"], "combined-root")
        except SystemExit as exc:
            assert "payload conflict" in str(exc), str(exc)
        else:
            raise AssertionError("changed authored set replayed under one operation ID")
        assert JournalSnapshot.open(run_dir).revision == revision

        # Subagent author: documents only; the source completion stays the terminal step.
        answer = json.loads(record(["--prepare-conclusion", "--role", "python-worker", "--lane-id", "sub-author",
            "--status", "pass", "--summary", "Own authored path: lib.py",
            "--artifact", "handoffs/sub-author.md", "--artifact", "checks/sub-author-edit.json",
            "--authored-path", "lib.py"], "combined-sub"))
        snapshot = JournalSnapshot.open(run_dir)
        artifact = json.loads(snapshot.read_text(answer["boundary"]))
        assert artifact["version"] == 2 and artifact["authored_paths"] == ["lib.py"]
        assert artifact["assignment"]["codex_thread_id"] == thread
        assert "lib.py" in snapshot.read_text("handoffs/sub-author.md")
        stored = json.loads(snapshot.read_text("delegation-summary.json"))
        registered = next(row for row in stored["subagents"] if row["lane_id"] == "sub-author")
        assert "handoff" not in registered and registered.get("status") != "pass"
        assert not any(event.get("lane_id") == "sub-author" and event.get("stage") == "handoff"
                       for event in (json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()))
        snapshot = JournalSnapshot.open(run_dir)
        workspace = registered_workspace(snapshot)
        author_coverage(snapshot, workspace, resolve_author_boundaries(snapshot, workspace))
        validator = load("combined_boundary_validator", "validate-run.py")
        for lane_id in ("root-author", "sub-author"):
            path = run_dir / f"checks/lane-boundary-{lane_id}.json"
            assert validator.validate_lane_boundary_artifact(path, str(path), lane_id, snapshot=JournalSnapshot.open(run_dir))[1] == []
        print("PASS combined author conclusion registers handoff and boundary in one transaction")


def main() -> int:
    native_author_fixtures()
    correction_state_fixtures()
    concurrent_boundary_fixture()
    combined_conclusion_fixtures()
    print("PASS lane boundary fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
