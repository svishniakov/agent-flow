#!/usr/bin/env python3
"""Fixture tests for record-lane-boundary.py."""

from __future__ import annotations

import json
import subprocess
import sys
import importlib.util
import tempfile
from pathlib import Path
from journal_io import JournalSnapshot, initialize_journal, transact
from verification_evidence import sha256, result_hash, empty_verification
from task_workspace import prepare, seal


ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "scripts" / "record-lane-boundary.py"


def native_author_fixtures():
    spec = importlib.util.spec_from_file_location("boundary_validator", ROOT / "scripts/validate-run.py")
    validator = importlib.util.module_from_spec(spec); spec.loader.exec_module(validator)
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
            handoff_bytes = f"Own authored path: {path}\n".encode(); proof_bytes = f"--- a/{path}\n+++ b/{path}\n-owned baseline\n+owned new {path}\n".encode()
            declaration = {"lane_id": lane_id, "authored_paths": [path], "handoff": {"path": handoff, "sha256": sha256(handoff_bytes)},
                "edit_evidence": [{"path": proof, "sha256": sha256(proof_bytes)}], "coauthors": {}}
            declaration_path = f"artifacts/{lane_id}-declaration.json"
            transact(run_dir, "declare-" + lane_id, {}, lambda current: ({handoff: handoff_bytes, proof: proof_bytes, declaration_path: json.dumps(declaration)}, {}))
            result = subprocess.run([sys.executable, str(RECORDER), "--run-dir", str(run_dir), "--lane-id", lane_id,
                "--declaration-json", declaration_path, "--operation-id", "boundary-" + lane_id], text=True, capture_output=True)
            assert result.returncode == 0, result.stderr
            snapshot = JournalSnapshot.open(run_dir)
            artifact = json.loads(snapshot.read_text(lane["boundary"]["changed_paths_artifact"]))
            assert artifact["changed_paths"] == sealed["changed_paths"] and artifact["authored_paths"] == [path]
            assert validator.validate_lane_boundary(run_dir, lane, lane_id, required=True, allow_pending=False, snapshot=snapshot)[1] == []
            forbidden = {**lane, "boundary": {**lane["boundary"], "forbidden_paths": [path]}}
            assert any("forbidden_paths" in error for error in validator.validate_lane_boundary(run_dir, forbidden, lane_id, required=True, allow_pending=False, snapshot=snapshot)[1])
            wrong = {**declaration, "authored_paths": ["outside-delta"]}
            transact(run_dir, "wrong-" + lane_id, {}, lambda current: ({declaration_path: json.dumps(wrong)}, {}))
            rejected = subprocess.run([sys.executable, str(RECORDER), "--run-dir", str(run_dir), "--lane-id", lane_id,
                "--declaration-json", declaration_path], text=True, capture_output=True)
            assert rejected.returncode != 0 and "handoff must confirm" in rejected.stderr, rejected.stderr
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
            ("overlap", {**original, "authored_paths": ["a", "b"]}, "coauthorship mismatch"),
            ("fresh-v1", {key: value for key, value in original.items() if key not in {"authored_paths", "handoff", "edit_evidence", "coauthors", "assignment"}} | {"version": 1}, "boundary version 2")]:
            transact(run_dir, operation, {}, lambda current: ({first_path: json.dumps(replacement)}, {}))
            assert any(marker in error for error in author_errors()), (operation, author_errors())
        # Shared file requires both independently disclosed authors; extra peers fail.
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
        root_handoff = b"Root authored a\n"
        root_proof = b"--- a/a\n+++ b/a\n+root own edit\n"
        root_declaration = {"lane_id": "root-author", "authored_paths": ["a"], "handoff": {"path": "handoffs/root-author.md", "sha256": sha256(root_handoff)},
            "edit_evidence": [{"path": "checks/root-edit.patch", "sha256": sha256(root_proof)}], "coauthors": {}}
        transact(run_dir, "declare-root", {}, lambda current: ({"handoffs/root-author.md": root_handoff, "checks/root-edit.patch": root_proof,
            "artifacts/root-declaration.json": json.dumps(root_declaration)}, {}))
        result = subprocess.run([sys.executable, str(RECORDER), "--run-dir", str(run_dir), "--lane-id", "root-author",
            "--declaration-json", "artifacts/root-declaration.json", "--operation-id", "boundary-root"], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert json.loads(JournalSnapshot.open(run_dir).read_text("checks/lane-boundary-root-author.json"))["assignment"]["codex_thread_id"] == root_id
        print("PASS impl018 native boundary v2 disjoint author permissions full delta forbidden and forged handoff controls")


def run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    if "--run-dir" in args:
        run_dir = Path(args[args.index("--run-dir") + 1]).resolve()
        initialize_journal(run_dir, {}, source_root=cwd.resolve(), result_contract_version=1)
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(args)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-lane-boundary-") as temp_dir:
        repo = Path(temp_dir).resolve() / "repo"
        repo.mkdir()

        run(["git", "init"], cwd=repo)
        run(["git", "config", "user.email", "fixture@example.com"], cwd=repo)
        run(["git", "config", "user.name", "Fixture"], cwd=repo)

        tracked_path = repo / "apps/api-service/src/routes/settings.ts"
        tracked_path.parent.mkdir(parents=True)
        tracked_path.write_text("export const value = 1;\n", encoding="utf-8")
        (repo / ".gitignore").write_text(".agent-work/\n", encoding="utf-8")
        run(["git", "add", "."], cwd=repo)
        run(["git", "commit", "-m", "base"], cwd=repo)

        tracked_path.write_text("export const value = 2;\n", encoding="utf-8")
        untracked_path = repo / "apps/shared/src/new.ts"
        untracked_path.parent.mkdir(parents=True)
        untracked_path.write_text("export const created = true;\n", encoding="utf-8")
        ignored_path = repo / ".agent-work/ignored.txt"
        ignored_path.parent.mkdir(parents=True)
        ignored_path.write_text("ignored\n", encoding="utf-8")

        run_dir = repo / ".agent-work/runs/lane-boundary-fixture"
        run_dir.mkdir(parents=True)

        result = run(
            [
                sys.executable,
                str(RECORDER),
                "--run-dir",
                str(run_dir),
                "--lane-id",
                "worker-a",
                "--base-ref",
                "HEAD",
            ],
            cwd=repo,
        )
        artifact_path = run_dir / "checks/lane-boundary-worker-a.json"
        if str(artifact_path) not in result.stdout:
            raise AssertionError("recorder stdout must include output artifact path")
        if not JournalSnapshot.open(run_dir).exists(artifact_path):
            raise AssertionError("recorder must write lane boundary artifact")

        data = json.loads(JournalSnapshot.open(run_dir).read_text(artifact_path))
        expected_tracked = ["apps/api-service/src/routes/settings.ts"]
        expected_untracked = ["apps/shared/src/new.ts"]
        if data.get("version") != 1:
            raise AssertionError("artifact version must be 1")
        if data.get("lane_id") != "worker-a":
            raise AssertionError("artifact lane_id must match requested lane")
        if data.get("status") != "captured":
            raise AssertionError("artifact status must be captured")
        if data.get("base_ref") != "HEAD":
            raise AssertionError("artifact base_ref must record HEAD")
        if data.get("head_ref") != "working-tree":
            raise AssertionError("artifact head_ref must default to working-tree")
        if data.get("tracked_changed_paths") != expected_tracked:
            raise AssertionError(
                f"tracked paths mismatch: {data.get('tracked_changed_paths')!r}"
            )
        if data.get("untracked_paths") != expected_untracked:
            raise AssertionError(
                f"untracked paths mismatch: {data.get('untracked_paths')!r}"
            )
        if data.get("changed_paths") != [*expected_tracked, *expected_untracked]:
            raise AssertionError(f"changed paths mismatch: {data.get('changed_paths')!r}")
        if any(".agent-work" in path for path in data.get("changed_paths", [])):
            raise AssertionError("ignored agent work files must not be captured")
        if "git diff --name-only HEAD" not in data.get("command", ""):
            raise AssertionError("artifact command must record tracked diff command")
        if data.get("notes") != "Boundary evidence for worker-a.":
            raise AssertionError("artifact notes must identify lane")

    native_author_fixtures()
    print("PASS record-lane-boundary fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
