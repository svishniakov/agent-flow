#!/usr/bin/env python3
"""Fixture tests for init-run Architecture Artifact Authoring Automation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from journal_io import JournalSnapshot
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INIT_RUN = ROOT / "scripts" / "init-run.py"
VALIDATE_RUN = ROOT / "scripts" / "validate-run.py"
DEFAULT_ARCHITECTURE_CONTEXT = {
    "product_context": ["saas-service"],
    "application_surface": ["backend-service"],
    "architecture_pattern": ["monolith"],
    "stack_runtime": ["go"],
    "risk_gates": ["migrations"],
    "verification_gates": ["unit", "integration"],
}
DEFAULT_ARCHITECTURE_CAPABILITIES = [
    "saas-platform-architecture",
    "go-backend-service-architecture",
    "modular-monolith-architecture",
]
AGENT_TODO_PLACEHOLDER = "TODO(agent):"


def run_init(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INIT_RUN), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def architecture_args(repo: Path, slug: str, *extra: str) -> list[str]:
    return [
        "--repo",
        str(repo),
        "--slug",
        slug,
        "--date",
        "2026-06-12",
        "--with-lanes",
        "--architecture-gate",
        "--budget",
        "standard",
        "--architecture-context-json",
        json.dumps(DEFAULT_ARCHITECTURE_CONTEXT),
        "--architecture-capabilities",
        ",".join(DEFAULT_ARCHITECTURE_CAPABILITIES),
        "--worker-lane",
        "worker-a:implementation:typescript-worker",
        "--summary",
        "Prepare SQLite verification environment before any worker edits",
        "--plan-summary",
        "Register planned architecture lanes and execute selected readiness checks",
        *extra,
    ]


def without_option(args: list[str], option: str, value_count: int = 1) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == option:
            index += 1 + value_count
            continue
        result.append(args[index])
        index += 1
    return result


def replace_option(args: list[str], option: str, values: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == option:
            result.extend([option, *values])
            index += 2
            continue
        result.append(args[index])
        index += 1
    return result


def expect_fail(name: str, args: list[str], needle: str) -> None:
    result = run_init(args)
    if result.returncode == 0:
        raise AssertionError(f"{name} expected failure")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"{name} missing {needle!r}\nOutput:\n{output}")


def expect_incomplete_run(name: str, run_dir: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATE_RUN),
            "--run-dir",
            str(run_dir),
            "--allow-pending",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 or "verification-readiness" not in result.stdout + result.stderr:
        raise AssertionError(
            f"{name} must remain incomplete even with --allow-pending\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def load_lane_map(run_dir: Path) -> dict[str, Any]:
    lane_map = json.loads(JournalSnapshot.open(run_dir).read_text("lane-map.json"))
    if not isinstance(lane_map, dict):
        raise AssertionError("lane-map.json must be an object")
    return lane_map


def check_writer_readiness(run_dir: Path, repo: Path, *, continuation=False) -> None:
    """Public intents and real process proof; no hand-authored service JSON."""
    from importlib.util import spec_from_file_location, module_from_spec
    from contextlib import redirect_stdout, redirect_stderr
    from io import StringIO
    spec = spec_from_file_location("readiness_recorder", ROOT / "scripts/record-agent-trace.py")
    recorder = module_from_spec(spec)
    spec.loader.exec_module(recorder)
    from verification_evidence import EvidenceError
    qa_events = []
    class SyntheticRootSource:
        def read(self, thread_id, *, event_indices=()):
            if thread_id == "00000000-0000-4000-8000-000000000002":
                return qa_events
            if thread_id != "00000000-0000-4000-8000-000000000001":
                raise EvidenceError("synthetic root session missing")
            return [{"type": "session_meta", "payload": {"id": thread_id}}]
    def root_trace(args):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            try:
                code = recorder.main(["--run-dir", str(run_dir), *args], session_source=SyntheticRootSource())
            except SystemExit as exc:
                code = 1
                output.write(str(exc))
        return code, output.getvalue()
    capture = repo.parent / "readiness-request.json"
    from verification_evidence import empty_verification
    verification = {**empty_verification(), "root_thread_id": "00000000-0000-4000-8000-000000000001",
                    "task_kind": "analysis" if continuation else "change", "blocker": "Selected environment checks have not yet been executed", "unverified": ["SQLite migration, unit and integration checks"],
                    "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                    "task_scope": {"path": "run.md", "section": "Task Scope"}}
    for script, args in (
        *([] if continuation else [("task-workspace.py", ["prepare", "--source", str(repo), "--destination", str(repo.parent / "readiness-workspace")])]),
        ("record-agent-trace.py", ["--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification",
                                   "--status", "active", "--summary", "Readiness fixture root and scope registered",
                                   "--verification-json", json.dumps(verification)]),
    ):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / script), "--run-dir", str(run_dir), *args],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
    def tree():
        return {str(path.relative_to(run_dir)): path.read_bytes()
                for path in run_dir.rglob("*") if path.is_file()}
    def record(kind: str, request: dict, identifier: str, *, ok=True):
        capture.write_text(json.dumps(request))
        if kind == "handoff" or kind in {"readiness-gate", "readiness-attempt"} and qa_events and ok:
            from unittest.mock import patch
            from journal_io import write_entry
            with patch("verification_evidence.CodexSessionSource", return_value=SyntheticRootSource()):
                return write_entry(run_dir, kind, request, identifier=identifier)
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/journal.py"),
                                 "--run-dir", str(run_dir), "record", "--kind", kind,
                                 "--request-file", str(capture), "--operation-id", identifier],
                                capture_output=True, text=True)
        if (result.returncode == 0) != ok:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout) if ok else result
    attempt = {"id": "readiness-first", "lane_id": "verification-readiness-1",
               "summary": "Selected gates have not yet been executed", "status": "blocked",
               "blockers": ["Missing selected gate process evidence"]}
    if not continuation:
        record("lane", {"id": "worker-a", "summary": "Future src implementation ownership", "status": "planned",
                        "lane_type": "implementation", "role": "typescript-worker", "execution_mode": "role-lane",
                        "wave": 3, "critical": True, "allowed_paths": ["src/**"]}, "readiness-worker-boundary")
    record("lane", {"id": "checkpoint-root", "summary": "Root environment checkpoint owner", "status": "planned", "lane_type": "architecture",
                    "role": "orchestrator", "execution_mode": "role-lane", "wave": 0, "critical": False}, "declare-checkpoint-root")
    root_code, root_output = root_trace([
                                 "--role", "orchestrator", "--execution-mode", "role-lane", "--lane-id", "checkpoint-root",
                                 "--stage", "spawned", "--status", "active", "--summary", "Own blocked environment checkpoint"])
    if root_code:
        raise AssertionError(root_output)
    record("readiness-attempt", attempt, "readiness-blocked")
    disposable = repo.parent / ("continuation-copy" if continuation else "readiness-copy")
    disposable.mkdir()
    failed = record("check", {"summary": "Environment verification is genuinely incomplete", "status": "pass", "cwd": str(disposable),
                              "command": [sys.executable, "-B", "-c", "print('selected environment checks not executed'); raise SystemExit(7)"]}, "readiness-incomplete-check")
    for axis in ("risk_gates", "verification_gates"):
        for facet in DEFAULT_ARCHITECTURE_CONTEXT[axis]:
            record("readiness-gate", {"attempt_id": attempt["id"], "axis": axis, "facet": facet, "summary": "Actual environment check remains incomplete",
                                      "status": "blocked", "check": "Selected environment proof not available", "evidence": [{"path": ref["path"]} for ref in failed["evidence"]]}, "initial-blocked-" + facet)
    record("harness-evaluation", {"summary": "Learning evaluation remains blocked with actual incomplete verification", "status": "blocked",
                                  "evidence": [{"path": ref["path"]} for ref in failed["evidence"]]}, "readiness-blocked-learning")
    if continuation:
        snapshot = JournalSnapshot.open(run_dir)
        closed = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/journal.py"), "--run-dir", str(run_dir), "finalize",
                                 "--expected-run-uuid", snapshot.run_uuid, "--expected-revision", str(snapshot.revision),
                                 "--expected-generation", str(snapshot.generation), "--operation-id", "readiness-blocked-final",
                                 "--summary", "Selected environment checks are not yet executed", "--status", "blocked"], capture_output=True, text=True)
        if closed.returncode:
            raise AssertionError(closed.stdout + closed.stderr)
        from journal_recovery import diagnose_recovery, recover
        diagnosis = diagnose_recovery(run_dir)
        recover(run_dir, upgrade=False, reopen=True, expected_run_uuid=diagnosis["identity"]["run_uuid"],
                expected_revision=diagnosis["revision"], expected_generation=diagnosis["generation"], identifier="readiness-native-reopen",
                reason="Execute actual environment checks", identity=diagnosis["identity"], final_index=diagnosis["final"]["index"], final_sha256=diagnosis["final"]["sha256"])
        checkpoint = record("event", {"summary": "Preserved actual blocked environment conclusion", "status": "blocked", "stage": "blocked-checkpoint",
                                      "role": "orchestrator", "lane_id": "checkpoint-root"}, "readiness-checkpoint")
    before = tree()
    if not continuation:
        start_code, start_output = root_trace(["--role", "typescript-worker",
                                "--execution-mode", "role-lane", "--lane-id", "worker-a",
                                "--stage", "spawned", "--status", "active", "--summary", "Must await readiness"])
        if start_code == 0 or "readiness" not in start_output.lower():
            raise AssertionError("unfinished readiness must block worker start: " + start_output)
        if tree() != before:
            raise AssertionError("rejected worker start must not allocate a request")
    record("readiness-attempt", {**attempt, "status": "pass", "blockers": []}, "readiness-without-proof", ok=False)
    if tree() != before:
        raise AssertionError("ready without gates must have zero mutations")
    endorsed_gates = []
    for axis in ("risk_gates", "verification_gates"):
        for facet in DEFAULT_ARCHITECTURE_CONTEXT[axis]:
            check = record("check", {"summary": "SQLite transaction environment verified for " + facet,
                                     "status": "pass", "cwd": str(disposable),
                                     "command": [sys.executable, "-B", "-c", "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('create table readiness(value integer)'); c.execute('insert into readiness values(7)'); c.commit(); assert c.execute('select value from readiness').fetchone() == (7,); print('actual SQLite transaction passed')"]}, "readiness-check-" + facet)
            endorsed_gates.append({"axis": axis, "facet": facet, "readiness": "ready", "check": "SQLite schema and committed data can be read",
                                   "notes": "Actual SQLite transaction passed", "evidence": check["evidence"]})
    # Source double supplies only runtime identity/completion; all writer transitions are public.
    from journal_io import prepare_review_input
    from agent_config import default_agents_dir, read_frontmatter, resolve_role_path, role_config
    snapshot = JournalSnapshot.open(run_dir)
    mapping = json.loads(snapshot.read_text("lane-map.json"))
    sources = ["run.md", "context.md", "plan.md"] + list({lane[field] for lane in mapping["lanes"] if lane["type"] == "architecture"
        for field in ("handoff", "architecture_design_brief") if lane.get(field) and snapshot.exists(lane[field])})
    pin = prepare_review_input(run_dir, attempt["lane_id"], sources, identifier="readiness-review-pin")
    qa_id = "00000000-0000-4000-8000-000000000002"
    root_id = verification["root_thread_id"]
    turn = qa_id + "-turn"
    spawn = {"parent_thread_id": root_id, "agent_role": "qa-verifier", "depth": 1}
    qa_events.extend([
        {"type": "session_meta", "payload": {"id": qa_id, "session_id": root_id, **spawn, "source": {"subagent": {"thread_spawn": spawn}}}},
        {"type": "event_msg", "timestamp": "2099-01-01T00:00:01+00:00", "payload": {"type": "task_started", "turn_id": turn}},
        {"type": "turn_context", "payload": {"turn_id": turn, "model": role_config(read_frontmatter(resolve_role_path(default_agents_dir(), "qa-verifier")), "qa-verifier")["model"]}},
    ])
    def qa_trace(args):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = recorder.main(["--run-dir", str(run_dir), *args], session_source=SyntheticRootSource())
        if result:
            raise AssertionError(output.getvalue())
        return json.loads(output.getvalue()) if "--prepare-conclusion" in args else output.getvalue()
    qa_trace(["--role", "qa-verifier", "--lane-id", attempt["lane_id"], "--stage", "spawned", "--status", "active", "--summary", "Independent synthetic applicability review",
              "--codex-thread-id", qa_id, "--review-input-operation-id", pin["operation_id"]])
    capture.write_text(json.dumps(endorsed_gates))
    handoff = next(lane for lane in mapping["lanes"] if lane["id"] == attempt["lane_id"])["handoff"]
    evidence_args = [argument for gate in endorsed_gates for ref in gate["evidence"] for argument in ("--artifact", ref["path"])]
    answer = qa_trace(["--prepare-conclusion", "--role", "qa-verifier", "--lane-id", attempt["lane_id"], "--status", "pass", "--summary", "Independent synthetic QA endorses selected SQLite checks",
                       "--artifact", handoff, "--readiness-gates-file", str(capture), *evidence_args])
    qa_events.extend([
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": json.dumps(answer)}]}},
        {"type": "event_msg", "timestamp": "2099-01-01T00:00:02+00:00", "payload": {"type": "task_complete", "turn_id": turn, "last_agent_message": json.dumps(answer)}},
    ])
    qa_trace(["--role", "qa-verifier", "--lane-id", attempt["lane_id"], "--stage", "handoff", "--status", "pass", "--summary", "Actual source double completed applicability review",
              "--codex-thread-id", qa_id, "--completion-turn-id", turn, "--artifact", handoff, *evidence_args])
    for gate in endorsed_gates:
            record("readiness-gate", {"attempt_id": attempt["id"], "axis": gate["axis"], "facet": gate["facet"],
                                      "summary": "Actual SQLite transaction passed", "status": "pass",
                                      "check": "SQLite schema and committed data can be read",
                                      "evidence": [{"path": ref["path"]} for ref in gate["evidence"]]}, "readiness-gate-" + gate["facet"])
    record("readiness-attempt", {**attempt, "summary": "All selected gate checks executed", "status": "pass", "blockers": []}, "readiness-ready")
    data = json.loads(JournalSnapshot.open(run_dir).read_text("verification-readiness.json"))
    if data["status"] != "ready" or data["approval_requests"] or data["approval_executions"]:
        raise AssertionError("real checks must close readiness without invented approvals")
    gates = data["attempts"][0]["gates"]
    if len(gates) != 3 or any(gate["readiness"] != "ready" for gate in gates):
        raise AssertionError("all three selected gates must have actual process proof")
    if not continuation:
        return
    refs = [{"path": ref["path"]} for ref in check["evidence"]]
    for lane_id, role, lane_type in (("continuation-qa", "qa-verifier", "qa"), ("continuation-review", "reviewer", "review")):
        record("lane", {"id": lane_id, "summary": "Recheck actual environment after checkpoint", "status": "planned", "lane_type": lane_type,
                        "role": role, "execution_mode": "role-lane", "wave": 4, "critical": False}, "declare-" + lane_id)
        record("handoff", {"lane_id": lane_id, "summary": "Rechecked actual selected environment process proof", "status": "pass", "evidence": refs}, "conclude-" + lane_id)
    blocker = {"id": "missing-environment-proof", "summary": "Actual selected environment commands now succeeded", "status": "pass", "evidence": refs}
    for status in ("blocked", "fail"):
        before = tree()
        record("continuation-blocker", {**blocker, "status": status}, "invalid-resolve-" + status, ok=False)
        if tree() != before:
            raise AssertionError("negative blocker observation must not become resolved")
    record("continuation-blocker", blocker, "environment-blocker-resolved")
    continuation = {"summary": "Resume after actual environment proof and independent rechecks", "status": "pass", "checkpoint": checkpoint["checkpoint"],
                    "readiness_lane": attempt["lane_id"], "historical_worker_lanes": [], "new_worker_lanes": [], "revalidated_lanes": [],
                    "qa_recheck_lane": "continuation-qa", "reviewer_recheck_lane": "continuation-review"}
    before = tree()
    record("continuation", {**continuation, "checkpoint": {**checkpoint["checkpoint"], "snapshot": "context.md"}}, "forged-checkpoint", ok=False)
    if tree() != before:
        raise AssertionError("forged checkpoint must have zero mutations")
    record("continuation", continuation, "environment-resumed")
    if json.loads(JournalSnapshot.open(run_dir).read_text("continuation-summary.json"))["status"] != "resumed-ready":
        raise AssertionError("actual checkpoint and readiness must support public continuation")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-init-run-tests-") as temp_dir:
        repo = Path(temp_dir).resolve() / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)

        base = architecture_args(repo, "arch-authoring")
        context_file = Path(temp_dir) / "context.json"
        context_file.write_text(json.dumps(DEFAULT_ARCHITECTURE_CONTEXT))
        for index, raw in enumerate((json.dumps(DEFAULT_ARCHITECTURE_CONTEXT, indent=4) + " " * 2048,
                                     " \n\t" + json.dumps(DEFAULT_ARCHITECTURE_CONTEXT),
                                     str(context_file))):
            result = run_init(replace_option(architecture_args(repo, f"json-context-{index}"),
                                             "--architecture-context-json", [raw]))
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        for index, raw in enumerate(("{broken" + " " * 2048, "[]", "null", "true", '"text"')):
            slug = f"invalid-json-context-{index}"
            expect_fail("invalid context rejected before writes",
                        replace_option(architecture_args(repo, slug), "--architecture-context-json", [raw]),
                        "JSON")
            if (repo / ".agent-work" / "runs" / f"2026-06-12-{slug}").exists():
                raise AssertionError("invalid context created a run")

        expect_fail(
            "architecture gate requires lane skeleton",
            without_option(base, "--with-lanes", 0),
            "--architecture-gate requires --with-lanes",
        )
        expect_fail(
            "architecture gate requires budget",
            without_option(architecture_args(repo, "missing-budget"), "--budget"),
            "--architecture-gate requires --budget",
        )
        expect_fail(
            "architecture gate requires architecture context",
            without_option(
                architecture_args(repo, "missing-context"),
                "--architecture-context-json",
            ),
            "--architecture-gate requires --architecture-context-json",
        )
        expect_fail(
            "architecture gate requires architecture capabilities",
            without_option(
                architecture_args(repo, "missing-capabilities"),
                "--architecture-capabilities",
            ),
            "--architecture-gate requires --architecture-capabilities",
        )
        expect_fail(
            "unknown architecture capability fails",
            replace_option(
                architecture_args(repo, "unknown-capability"),
                "--architecture-capabilities",
                ["unknown-capability"],
            ),
            "unknown architecture capability: unknown-capability",
        )
        expect_fail(
            "malformed worker lane fails",
            replace_option(
                architecture_args(repo, "malformed-worker"),
                "--worker-lane",
                ["worker-a:implementation"],
            ),
            "--worker-lane must use lane-id:type:role",
        )

        result = run_init(base)
        if result.returncode != 0:
            raise AssertionError(
                "valid architecture-gate init expected pass\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

        run_dir = Path(result.stdout.strip())
        snapshot = JournalSnapshot.open(run_dir)
        for name, content in snapshot.documents.items():
            if content is not None and AGENT_TODO_PLACEHOLDER.encode() in content:
                raise AssertionError(f"init must not manufacture proof placeholders: {name}")
        for name in ("claim-evidence.json", "acceptance-traceability.json", "verification-readiness.json"):
            if snapshot.exists(name):
                raise AssertionError(f"unknown proof document must not be fabricated: {name}")
        lane_map = load_lane_map(run_dir)
        if lane_map.get("schema_version") != 2:
            raise AssertionError("generated lane-map.json must use schema v2")
        if lane_map.get("handoff_state_required") is not True:
            raise AssertionError("architecture run must retain Handoff State Gate")
        if lane_map.get("architecture_context") != DEFAULT_ARCHITECTURE_CONTEXT:
            raise AssertionError("selected architecture context must be retained")
        if lane_map.get("architecture_capabilities", {}).get("selected") != DEFAULT_ARCHITECTURE_CAPABILITIES:
            raise AssertionError("selected capabilities must be retained")
        scope = lane_map.get("engineering_simplicity_scope", {})
        if scope.get("evidence"):
            raise AssertionError("init must not invent simplicity evidence")
        if any(lane.get("status") != "planned" for lane in lane_map["lanes"]):
            raise AssertionError("init must leave planned work unfinished")

        delegation_summary = json.loads(JournalSnapshot.open(run_dir).read_text("delegation-summary.json"))
        if delegation_summary.get("subagents_used") is not False:
            raise AssertionError("generated delegation-summary.json must start with subagents_used=false")
        if delegation_summary.get("role_lanes_used") is not False:
            raise AssertionError("generated delegation-summary.json must start with role_lanes_used=false")

        final_text = JournalSnapshot.open(run_dir).read_text("final.md")
        if "Verdict: ship" in final_text or "Verdict: pass-with-risks" in final_text:
            raise AssertionError("init must not claim successful final acceptance")

        lane_ids = {lane.get("id") for lane in lane_map.get("lanes", []) if isinstance(lane, dict)}
        expected_lane_ids = {
            "architecture-contract",
            "verification-readiness-1",
            "worker-a",
            "qa-behavior",
            "review-contract",
        }
        missing_lane_ids = expected_lane_ids - lane_ids
        if missing_lane_ids:
            raise AssertionError(f"generated lane-map.json missing lanes: {sorted(missing_lane_ids)}")
        for lane_data in lane_map.get("lanes", []):
            if not isinstance(lane_data, dict):
                continue
            state = lane_data.get("handoff_state")
            if not isinstance(state, dict):
                raise AssertionError(f"generated lane {lane_data.get('id')} missing handoff_state")
            if state.get("version") != 1:
                raise AssertionError("generated handoff_state.version must be 1")
            if state.get("mode") != "task":
                raise AssertionError("generated handoff_state.mode must be task")
            if state.get("status") != "queued":
                raise AssertionError("generated handoff_state.status must start queued")
            if state.get("to") != lane_data.get("id"):
                raise AssertionError("generated handoff_state.to must match lane id")
            if state.get("handoff") != lane_data.get("handoff"):
                raise AssertionError("generated handoff_state.handoff must match lane handoff")
        reviewer_lane = next(
            (lane for lane in lane_map.get("lanes", []) if lane.get("id") == "review-contract"),
            None,
        )
        if not isinstance(reviewer_lane, dict):
            raise AssertionError("generated lane-map.json must include review-contract lane")
        if reviewer_lane.get("execution_mode") != "subagent":
            raise AssertionError("generated reviewer lane must use execution_mode=subagent")
        summary = json.loads(JournalSnapshot.open(run_dir).read_text("delegation-summary.json"))
        if not isinstance(summary.get("verification"), dict):
            raise AssertionError("generated summary must include verification independently of lane-map")

        expect_incomplete_run("generated pending run", run_dir)
        check_writer_readiness(run_dir, repo)
        continuation_result = run_init(without_option(architecture_args(repo, "continuation-observations"), "--worker-lane"))
        if continuation_result.returncode:
            raise AssertionError(continuation_result.stdout + continuation_result.stderr)
        check_writer_readiness(Path(continuation_result.stdout.strip()), repo, continuation=True)

        plain_result = run_init(
            [
                "--repo",
                str(repo),
                "--slug",
                "plain-lanes",
                "--date",
                "2026-06-12",
                "--with-lanes",
            ]
        )
        if plain_result.returncode != 0:
            raise AssertionError(
                "plain --with-lanes init expected pass\n"
                f"STDOUT:\n{plain_result.stdout}\nSTDERR:\n{plain_result.stderr}"
            )
        plain_lane_map = load_lane_map(Path(plain_result.stdout.strip()))
        if "handoff_state_required" in plain_lane_map:
            raise AssertionError("plain --with-lanes must not opt into Handoff State Gate")
        for lane_data in plain_lane_map.get("lanes", []):
            if isinstance(lane_data, dict) and "handoff_state" in lane_data:
                raise AssertionError("plain --with-lanes must not prefill handoff_state")

    print("PASS init-run fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
