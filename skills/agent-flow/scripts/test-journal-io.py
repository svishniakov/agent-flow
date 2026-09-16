#!/usr/bin/env python3
"""Journal regressions. All session records here are explicitly synthetic."""
import json
import os
import sys
import subprocess
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path


def load(name, filename):
    spec = spec_from_file_location(name, Path(__file__).with_name(filename))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load("journal_fixtures", "test-verification-evidence.py")
recorder = load("journal_recorder", "record-agent-trace.py")
import verification_evidence as evidence
import journal_io


class JournalRegressions(unittest.TestCase):
    def setUp(self):
        base.EvidenceRegressions.setUp(self)
        self.open_timeline()

    def snapshot(self):
        if (self.run / ".journal/state.sqlite3").exists():
            return {name: data for name, data in journal_io.JournalSnapshot.open(self.run).documents.items() if data is not None}
        return {p.relative_to(self.run).as_posix(): p.read_bytes() for p in self.run.rglob("*") if p.is_file()}

    def record(self, *args):
        if "--prepare-conclusion" not in args:
            try:
                base.initialize_open_fixture(self.run)
            except (journal_io.JournalError, OSError) as exc:
                raise SystemExit(str(exc)) from exc
        with redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()):
            recorder.main(["--run-dir", str(self.run), *args], session_source=self.source)
        return output.getvalue()

    def repeat_args(self, role="qa-verifier"):
        record = self.summary["subagents"][0 if role == "qa-verifier" else 1]
        return ["--role", role, "--lane-id", record["lane_id"], "--stage", "handoff", "--status", "pass",
                "--summary", "Repeat", "--completion-turn-id", record["completion_turn_id"],
                "--artifact", record["handoff"], "--artifact", "checks.md"]

    def open_timeline(self):
        path = self.run / "timeline.jsonl"
        lines = path.read_text().splitlines()
        if lines and json.loads(lines[-1]).get("stage") == "final":
            path.write_text("\n".join(lines[:-1]) + "\n")

    def test_assignment_stage_status_commands(self):
        allowed = {"spawned": {"active"}, "handoff": {"pass", "pass-with-risks"},
                   "blocked": {"blocked"}, "fail": {"fail"}}
        base.initialize_open_fixture(self.run)
        for mode in ("subagent", "role-lane"):
            for stage, statuses in allowed.items():
                for status in ("active", "pass", "pass-with-risks", "blocked", "fail", "done"):
                    with self.subTest(mode=mode, stage=stage, status=status):
                        lane = f"{mode}-{stage}-{status}"
                        args = ["--role", "qa-verifier", "--execution-mode", mode, "--lane-id", lane,
                                "--stage", stage, "--status", status, "--summary", "State matrix",
                                "--artifact", "checks.md"]
                        if mode == "subagent":
                            args += ["--codex-thread-id", base.QA_ID]
                        before = journal_io.JournalSnapshot.open(self.run)
                        requests = {p.name: p.read_bytes() for p in (self.run / '.journal/requests').glob('*')}
                        if status in statuses:
                            if mode == "subagent" and stage != "spawned":
                                self.record("--role", "qa-verifier", "--lane-id", lane, "--codex-thread-id", base.QA_ID,
                                            "--stage", "spawned", "--status", "active", "--summary", "Own spawn")
                            self.record(*args)
                        else:
                            with patch.object(recorder, "ensure_result_contract", side_effect=AssertionError("too late")), self.assertRaisesRegex(SystemExit, "requires status"):
                                self.record(*args)
                            after = journal_io.JournalSnapshot.open(self.run)
                            self.assertEqual((before.revision, before.documents, before.receipts),
                                             (after.revision, after.documents, after.receipts))
                            self.assertEqual(requests, {p.name: p.read_bytes() for p in (self.run / '.journal/requests').glob('*')})

    def test_new_event_guard_covers_append_and_publish_histories(self):
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        bad = {**base.trace_event(self.summary['subagents'][0], 'spawned'), 'status': 'pass', 'lane_id': 'bad'}
        journal_io.validate_event(bad)  # Historical facts retain their original validation contract.
        for path in ('timeline.jsonl', 'agents/qa-verifier/trace.jsonl', 'agents/new/trace.jsonl'):
            with self.subTest(path=path), self.assertRaisesRegex(journal_io.JournalError, 'requires status'):
                journal_io.transact(self.run, 'bad-' + path, {}, lambda current: (
                    {path: current.documents.get(path, b'') + (json.dumps(bad) + '\n').encode()}, {}))
        with self.assertRaisesRegex(journal_io.JournalError, 'requires status'):
            journal_io.append_event(self.run / 'timeline.jsonl', bad, identifier='bad-append')
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual((before.revision, before.documents, before.receipts), (after.revision, after.documents, after.receipts))
        self.assertFalse((self.run / '.journal/requests/bad-append').exists())

    def test_repeat_reviewer_and_reordered_evidence(self):
        before = self.snapshot()
        for role in ("qa-verifier", "reviewer"):
            args = self.repeat_args(role)
            args[-4:] = ["--artifact", "checks.md", "--artifact", args[-3]]
            self.assertIn("unchanged", self.record(*args))
        self.assertEqual(before, self.snapshot())

    def test_saved_source_receipt_allows_reordered_evidence(self):
        before = self.snapshot()
        for role in ("qa-verifier", "reviewer"):
            args = self.repeat_args(role)
            self.assertIn("unchanged", self.record(*args))
            args[-4:] = ["--artifact", "checks.md", "--artifact", args[-3]]
            self.assertIn("unchanged", self.record(*args))
        self.assertEqual(before, self.snapshot())

    def test_saved_source_receipt_ignores_transport_and_summary(self):
        before = self.snapshot()
        for role in ("qa-verifier", "reviewer"):
            args = self.repeat_args(role)
            record = self.summary["subagents"][0 if role == "qa-verifier" else 1]
            args.extend(["--codex-thread-id", record["codex_thread_id"]])
            self.assertIn("unchanged", self.record(*args))
            args[args.index("--summary") + 1] = "Technical retry with different display text"
            args.extend(["--artifact", "checks.md", "--resolve-session", "--agent-path", "/root/verifier"])
            record = self.summary["subagents"][0 if role == "qa-verifier" else 1]
            resolved = {"codex_thread_id": record["codex_thread_id"], "agent_path": "/root/verifier",
                        "observed_spawn_at": "2026-01-01T00:00:00Z", "session_meta_event": 1,
                        "task_started_event": 2}
            with patch.object(self.source, "resolve_session", return_value=resolved, create=True):
                self.assertIn("unchanged", self.record(*args))
        self.assertEqual(before, self.snapshot())

    def test_repeat_revalidates_source_and_evidence(self):
        for mutation, message in (("source", "unfinished"), ("evidence", "sha256"), ("handoff", "sha256"), ("result", "current result")):
            with self.subTest(mutation=mutation):
                summary, source = base.acceptance_pack(self.run)
                self.open_timeline()
                self.summary, self.source = summary, source
                if mutation == "source":
                    self.source.sessions[base.QA_ID].append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "new"}})
                else:
                    path = {"evidence": self.run / "checks.md", "handoff": self.run / "handoffs/evidence-qa.md", "result": self.root / "result.txt"}[mutation]
                    path.write_text(path.read_text() + "Changed\n")
                if (self.run / ".journal/state.sqlite3").exists():
                    documents, _ = journal_io.capture_flat(self.run)
                    journal_io.transact(self.run, "fixture-" + mutation, {}, lambda snapshot: (documents, {}))
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, message):
                    self.record(*self.repeat_args())
                self.assertEqual(before, self.snapshot())

    def test_new_qa_turn_invalidates_reviewer(self):
        self.open_timeline()
        base.workspace_pack(self)
        events = self.source.sessions[base.QA_ID]
        for event in events:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = "new-qa-turn"
        args = self.repeat_args()
        args[args.index("--completion-turn-id") + 1] = "new-qa-turn"
        with self.assertRaisesRegex(SystemExit, "terminal assignment"):
            self.record(*args)
        args[args.index("--lane-id") + 1] = "fresh-qa"
        args.extend(["--codex-thread-id", base.QA_ID])
        self.record("--role", "qa-verifier", "--lane-id", "fresh-qa", "--codex-thread-id", base.QA_ID,
                    "--stage", "spawned", "--status", "active", "--summary", "Fresh QA assignment")
        self.record(*args)
        stored = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        self.assertIsNone(stored["verification"]["reviewer"])

    def test_prepare_conclusion_is_read_only_and_requires_completion(self):
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        args = ["--prepare-conclusion", "--role", "qa-verifier", "--lane-id", "evidence-qa", "--status", "fail",
                "--artifact", "handoffs/evidence-qa.md", "--artifact", "checks.md"]
        self.source.sessions[base.QA_ID] = self.source.sessions[base.QA_ID][:3]
        before = self.snapshot()
        output = self.record(*args)
        self.assertEqual(json.loads(output)["verdict"], "fail")
        self.assertEqual(before, self.snapshot())
        with self.assertRaisesRegex(SystemExit, "completed turn missing"):
            self.record(*self.repeat_args())
        self.assertEqual(before, self.snapshot())

    def test_invalid_modes_and_missing_assignment_do_not_write(self):
        for args in (["--render-final", "--prepare-conclusion"], ["--prepare-conclusion", "--role", "qa-verifier"],
                     ["--render-final", "--stage", "final"], ["--role", "orchestrator", "--status", "pass"],
                     ["--prepare-conclusion", "--role", "reviewer", "--status", "pass", "--lane-id", "unknown"]):
            before = self.snapshot()
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.record(*args)
            self.assertEqual(before, self.snapshot())
        for option in ("--wave", "--next-step", "--execution-mode", "--runtime-nickname", "--stable-agent-name"):
            args = ["--render-final", option, "1" if option == "--wave" else "subagent"]
            before = self.snapshot()
            with self.subTest(option=option), self.assertRaises(SystemExit):
                self.record(*args)
            self.assertEqual(before, self.snapshot())

    def test_repeat_checks_independent_boundary_declaration(self):
        base.write_json(self.run / "lane-map.json", {"lanes": [{"id": "worker", "boundary": {"changed_paths_artifact": "boundary.json"}}]})
        base.write_json(self.run / "boundary.json", {"changed_paths": ["omitted.txt"]})
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "omits run-owned paths: omitted.txt"):
            self.record(*self.repeat_args())
        self.assertEqual(before, self.snapshot())

    def test_prepare_reviewer_revalidates_source_bound_qa(self):
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        handoff = self.run / "handoffs/evidence-qa.md"
        handoff.write_text(handoff.read_text() + "Changed QA\n")
        self.summary["subagents"][0]["handoff_sha256"] = evidence.sha256(handoff.read_bytes())
        base.write_json(self.run / "delegation-summary.json", self.summary)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "source handoff_sha256 mismatch"):
            self.record("--prepare-conclusion", "--role", "reviewer", "--lane-id", "evidence-reviewer", "--status", "pass", "--artifact", "handoffs/evidence-reviewer.md", "--artifact", "checks.md")
        self.assertEqual(before, self.snapshot())

    def test_reviewer_cannot_adopt_qa_handoff(self):
        self.open_timeline()
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        qa = self.summary["subagents"][0]
        alias = self.run / "handoffs/qa-alias.md"
        alias.symlink_to("evidence-qa.md")
        hardlink = self.run / "handoffs/qa-hardlink.md"
        hardlink.hardlink_to(self.run / qa["handoff"])
        for handoff in (qa["handoff"], str(self.run / qa["handoff"]), "handoffs/qa-alias.md", "handoffs/qa-hardlink.md"):
            with self.subTest(handoff=handoff):
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "reviewer must use its own handoff"):
                    self.record("--prepare-conclusion", "--role", "reviewer", "--lane-id", "evidence-reviewer", "--status", "pass", "--artifact", handoff, "--artifact", "checks.md")
                self.assertEqual(before, self.snapshot())
                canonical = journal_io.display_path(handoff, self.run.resolve())
                answer = {"verdict": "passed", "reviewed_result_hash": qa["reviewed_result_hash"],
                          "handoff": canonical, "handoff_sha256": qa["handoff_sha256"], "qa_handoff_sha256": qa["handoff_sha256"]}
                self.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "reviewer", answer, 20)
                args = self.repeat_args("reviewer")
                args[-3] = handoff
                with self.assertRaisesRegex(SystemExit, "reviewer must use its own handoff"):
                    self.record(*args)
                self.assertEqual(before, self.snapshot())
                stored = json.loads((self.run / "delegation-summary.json").read_text())
                stored["subagents"][1].update(handoff=canonical, handoff_sha256=qa["handoff_sha256"])
                errors = evidence.validate_verification(self.run, stored, {}, "ship", self.source)
                self.assertIn("reviewer must use its own handoff", "\n".join(errors))

    def test_distinct_reviewer_handoff_may_have_identical_bytes(self):
        self.open_timeline()
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        reviewer = self.summary["subagents"][1]
        (self.run / reviewer["handoff"]).write_bytes((self.run / self.summary["subagents"][0]["handoff"]).read_bytes())
        base.workspace_pack(self)
        answer = json.loads(self.record("--prepare-conclusion", "--role", "reviewer", "--lane-id", "evidence-reviewer", "--status", "pass", "--artifact", reviewer["handoff"], "--artifact", "checks.md"))
        self.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "reviewer", answer, 20)
        self.record(*self.repeat_args("reviewer"))
        before = self.snapshot()
        self.assertIn("unchanged", self.record(*self.repeat_args("reviewer")))
        self.assertEqual(before, self.snapshot())
        stored = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        self.assertEqual(evidence.validate_verification(self.run, stored, {}, "ship", self.source), [])

    def test_wc04_fresh_change_cannot_omit_or_fake_workspace(self):
        documents, _ = journal_io.capture_flat(self.run)
        journal_io.initialize_journal(self.run, documents, source_root=self.root)
        for fake in ({}, {"result_contract_version": 1, "legacy": True, "workspace": None}):
            summary = json.loads(json.dumps(self.summary))
            summary.update(fake)
            errors = evidence.validate_verification(self.run, summary, {}, "ship", self.source)
            self.assertIn("registered and sealed workspace", "; ".join(errors))

    def test_wc04_historical_replay_does_not_authorize_new_qa_turn(self):
        self.assertIn("unchanged", self.record(*self.repeat_args()))
        before = self.snapshot()
        for event in self.source.sessions[base.QA_ID]:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = "fresh-uncovered-turn"
        args = self.repeat_args()
        args[args.index("--completion-turn-id") + 1] = "fresh-uncovered-turn"
        with self.assertRaisesRegex(SystemExit, "registered and sealed workspace"):
            self.record(*args)
        self.assertEqual(before, self.snapshot())

    def test_wc04_published_workspace_pointer_cannot_replace_receipt_binding(self):
        base.workspace_pack(self)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        receipt = snapshot.operation_receipt("workspace-prepare")
        document = receipt["result"]["document"]
        data = json.loads(snapshot.read_bytes(document))
        data["baseline_manifest"]["exclusions"].append("result.txt")
        journal_io.transact(self.run, "fixture-forged-pointer", {}, lambda current: ({document: json.dumps(data)}, {}))
        with self.assertRaisesRegex(journal_io.JournalError, "workspace document changed"):
            evidence.result_hash(self.run, self.summary["verification"])

    def test_wc04_boundary_uses_full_scanner_delta_and_rejects_omission(self):
        workspace = base.workspace_pack(self, files=["undeclared\nЮникод.txt", "back\\slash"])
        boundary = load("workspace_boundary", "record-lane-boundary.py")
        path = boundary.capture_boundary(run_dir=self.run, lane_id="worker", repo_root=None,
                                         base_ref="HEAD", head_ref="working-tree")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        actual = json.loads(snapshot.read_bytes(path))
        self.assertEqual(actual["changed_paths"], self.summary["verification"]["result_files"])
        self.assertEqual(base.validator.validate_lane_boundary_artifact(path, str(path), "worker", snapshot=snapshot)[1], [])
        actual["changed_paths"] = []
        journal_io.transact(self.run, "fixture-omitted-boundary", {},
                            lambda current: ({path.relative_to(self.run).as_posix(): json.dumps(actual)}, {}))
        errors = base.validator.validate_lane_boundary_artifact(path, str(path), "worker", snapshot=journal_io.JournalSnapshot.open(self.run))[1]
        self.assertIn("full registered workspace delta", "; ".join(errors))

    def test_wc04_mode_only_reseal_requires_new_acceptance(self):
        from task_workspace import seal
        workspace = base.workspace_pack(self)
        original_hash = self.summary["verification"]["result_hash"]
        (Path(workspace["working_root"]) / "result.txt").chmod(0o755)
        sealed = seal(self.run)
        self.assertEqual(sealed["changed_paths"], ["result.txt"])
        self.assertNotEqual(evidence.result_hash(self.run, self.summary["verification"]), original_hash)
        errors = evidence.validate_verification(self.run, self.summary, {}, "ship", self.source)
        self.assertIn("stale", "; ".join(errors))

    def test_wc_delivery_requires_current_full_candidate_and_acceptance(self):
        from task_workspace import delivery
        workspace = base.workspace_pack(self)
        self.record("--render-final")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        for record in summary["subagents"]:
            record["status"] = "pass"
            record["obligation"] = {"id": record["role"], "required": True, "state": "current"}
        journal_io.transact(self.run, "obligations", {}, lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
        from journal_lifecycle import finalize
        snapshot = journal_io.JournalSnapshot.open(self.run)
        finalize(self.run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                 expected_generation=1, identifier="fixture-finalize", final_bytes=snapshot.read_bytes("final.md"),
                 verdict="ship", session_source=self.source)
        delivered = delivery(self.run, session_source=self.source)
        before = self.snapshot()
        self.assertEqual(delivered, delivery(self.run, session_source=self.source))
        self.assertEqual(before, self.snapshot())
        self.assertTrue(Path(delivered["description"]).is_file())
        (self.root / "foreign-new.txt").write_text("Foreign source edit stays outside the retained candidate.")
        self.assertEqual(delivered, delivery(self.run, session_source=self.source))
        candidate = Path(delivered["candidate_root"])
        (candidate / "undeclared.txt").write_text("Hidden change")
        with self.assertRaisesRegex(journal_io.JournalError, "candidate changed"):
            delivery(self.run, session_source=self.source)
        self.assertEqual(before, self.snapshot())

    def test_invalid_stages_and_final_order_do_not_write(self):
        for role, stage in (("orchestrator", "final"), ("qa-verifier", "final"), ("qa-verifier", "spawn")):
            before = self.snapshot()
            with self.subTest(stage=stage), self.assertRaises(SystemExit):
                self.record("--role", role, "--stage", stage, "--status", "pass", "--summary", "Invalid")
            self.assertEqual(before, self.snapshot())

    def test_open_target_failure_preserves_existing_files(self):
        self.open_timeline()
        target = self.run / "agents/orchestrator/trace.jsonl"
        target.mkdir(parents=True)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "write target must be a file"):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--status", "active", "--summary", "Pending", "--artifact", "checks.md")
        self.assertEqual(before, self.snapshot())

    def test_open_error_does_not_truncate_existing_target(self):
        self.open_timeline()
        target = self.run / "agents/orchestrator/trace.jsonl"
        target.parent.mkdir(parents=True)
        target.write_text('{"existing": "trace bytes"}\n')
        before = self.snapshot()
        base.initialize_open_fixture(self.run)
        with patch.object(journal_io, "_put_documents", side_effect=PermissionError("synthetic target open failure")), self.assertRaisesRegex(SystemExit, "synthetic target open failure"):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "active", "--summary", "Pending", "--artifact", "checks.md")
        self.assertEqual(before, self.snapshot())

    def test_symlink_write_target_parent_rejected(self):
        self.open_timeline()
        (self.run / "agents/orchestrator").symlink_to(self.root, target_is_directory=True)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "symlink"):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "active", "--summary", "Pending")
        self.assertEqual(before, self.snapshot())

    def test_invalid_verification_type_and_coverage_preserve_bytes(self):
        self.open_timeline()
        for value in (False, ["result.txt", "missing.txt"]):
            data = dict(self.summary["verification"], run_changed_files=value)
            before = self.snapshot()
            with self.assertRaises(SystemExit):
                self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--status", "active", "--summary", "Pending", "--verification-json", json.dumps(data))
            self.assertEqual(before, self.snapshot())

    def test_render_twice_preserves_prose_and_escapes_paths(self):
        path = "имя с пробелом `<tag>.txt"
        self.summary["verification"]["run_changed_files"] = [path]
        base.write_json(self.run / "delegation-summary.json", self.summary)
        prose = "# Отчёт\n\nСоседний список:\n- чужой файл\n\nРусское пояснение.\n"
        final = self.run / "final.md"
        final.write_text(prose)
        base.workspace_pack(self, files=[path])
        self.record("--render-final")
        first = journal_io.JournalSnapshot.open(self.run).read_text("final.md")
        self.record("--render-final")
        self.assertEqual(first, journal_io.JournalSnapshot.open(self.run).read_text("final.md"))
        self.assertTrue(first.startswith(prose))
        self.assertIn("&#96;&lt;tag&gt;", first)
        self.assertIn("Role Lanes: none", first)
        self.assertEqual(first.count("agent-flow:worktree:begin"), 1)

    def test_unsafe_declared_paths_rejected(self):
        outside = self.root.parent / "outside-journal-result"
        link = self.root / "escape"
        link.symlink_to(self.root.parent, target_is_directory=True)
        for value in ("../outside", "escape/outside-journal-result"):
            with self.subTest(path=value), self.assertRaises(evidence.EvidenceError):
                evidence.changed_paths(self.run, {}, {"run_changed_files": [value]})

    def test_source_time_negative_types_and_precision(self):
        for field in ("completed_at", "timestamp"):
            for value in (None, False, [], 1.25, 1789031492000, "2026-09-10T09:00:00", "bad"):
                event = {"payload": {"completed_at": 1789031492}, "timestamp": "2026-09-10T09:11:33.015Z"}
                (event["payload"] if field == "completed_at" else event)[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(evidence.EvidenceError):
                    journal_io.source_time(event, "completed_at")
        with self.assertRaises(evidence.EvidenceError):
            journal_io.source_time({"payload": {}}, "completed_at")
        stamp = journal_io.source_time({"payload": {}, "timestamp": "2026-09-10T09:00:00Z"}, "completed_at")
        self.assertEqual(stamp.event_origin, "outer")
        self.assertIsNone(stamp.event_before)

    def test_source_time_contract_origins(self):
        published = "2026-09-10T09:11:33.015Z"
        event = {"payload": {"completed_at": 1789031492}, "timestamp": published}
        inner = journal_io.source_time(event, "completed_at")
        self.assertEqual(inner.event_origin, "payload")
        self.assertEqual(inner.event_at.isoformat(), "2026-09-10T09:11:32+00:00")
        self.assertEqual((inner.event_before - inner.event_at).total_seconds(), 1)
        self.assertEqual(inner.observed_at, evidence.timestamp(published))
        outer = journal_io.source_time({"payload": {}, "timestamp": published}, "completed_at")
        self.assertEqual(outer.event_origin, "outer")
        self.assertEqual(outer.event_at, inner.observed_at)
        self.assertIsNone(outer.event_before)
        self.assertEqual(outer.observed_at, outer.event_at)

    def test_iso_without_fraction_keeps_point_and_publication_order(self):
        first = journal_io.source_time({"payload": {}, "timestamp": "2026-09-10T09:00:00Z"}, "completed_at")
        self.assertIsNone(first.event_before)
        self.assertEqual(first.observed_at, first.event_at)
        later = journal_io.source_time({"payload": {}, "timestamp": "2026-09-10T09:00:01Z"}, "completed_at")
        first_record = {"completed_at": first.event_at, "completed_before": first.event_before, "published_at": first.observed_at}
        later_record = {"completed_at": later.event_at, "completed_before": later.event_before, "published_at": later.observed_at}
        self.assertTrue(evidence.completion_follows(later_record, first_record))
        self.assertFalse(evidence.completion_follows(first_record, later_record))
        self.assertFalse(evidence.completion_follows(first_record, first_record))

    def test_observed_reviewer_next_second_and_equal_order(self):
        events = self.source.sessions[base.REVIEWER_ID]
        events[1]["timestamp"] = "2026-09-10T09:00:00Z"
        events[-1]["payload"]["completed_at"] = int(datetime.fromisoformat("2026-09-10T09:33:50+00:00").timestamp())
        events[-1]["timestamp"] = "2026-09-10T09:33:51.022Z"
        before = json.dumps(events)
        result = evidence.completed_turn(self.source, base.REVIEWER_ID, base.REVIEWER_ID + "-turn", base.ROOT_ID, "reviewer")
        self.assertEqual(result["completed_at"].isoformat(), "2026-09-10T09:33:50+00:00")
        self.assertFalse(evidence.completion_follows(result, result))
        self.assertEqual(before, json.dumps(events))

    def test_four_writers_use_one_injected_utc_clock(self):
        init = load("journal_init", "init-run.py")
        append = load("journal_append", "append-timeline.py")
        handoff = load("journal_handoff", "record-handoff-state.py")
        stamp = "2026-09-13T12:34:56.123456Z"
        for zone in ("UTC", "Pacific/Honolulu"):
            with self.subTest(zone=zone), patch.dict(os.environ, {"TZ": zone}):
                with patch.object(journal_io, "datetime") as clock:
                    clock.now.return_value = datetime(2026, 9, 13, 12, 34, 56, 123456, tzinfo=timezone.utc)
                    self.assertEqual(journal_io.now_iso(), stamp)
                for module in (init, append, recorder, handoff):
                    self.assertIs(module.now_iso, journal_io.now_iso)
                slug = zone.replace("/", "-").lower()
                with patch.object(init, "now_iso", return_value=stamp), patch.object(init, "datetime") as local_clock, redirect_stdout(StringIO()) as output:
                    local_clock.now.return_value.astimezone.return_value.strftime.return_value = "2026-09-12"
                    init.main(["--repo", str(self.root), "--slug", slug, "--mode", "compact"])
                run = Path(output.getvalue().strip())
                self.assertEqual(run.name, "2026-09-12-" + slug)
                with patch.object(journal_io, "now_iso", return_value=stamp), patch.object(sys, "argv", ["append-timeline.py", "--run-dir", str(run), "--stage", "checks", "--role", "orchestrator", "--status", "pass", "--summary", "Checked"]), redirect_stdout(StringIO()):
                    append.main()
                base.write_json(run / "lane-map.json", {"lanes": [{"id": "worker", "handoff": "handoffs/worker.md"}]})
                journal_io.transact(run, "fixture-lanes", {}, lambda snapshot: ({"lane-map.json": (run / "lane-map.json").read_bytes()}, {}))
                with patch.object(journal_io, "now_iso", return_value=stamp), redirect_stdout(StringIO()):
                    handoff.main(["--run-dir", str(run), "--lane-id", "worker", "--status", "queued"])
                with patch.object(recorder, "now_iso", return_value=stamp), redirect_stdout(StringIO()):
                    recorder.main(["--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "pass", "--summary", "Checked"])
                snapshot = journal_io.JournalSnapshot.open(run)
                events = [json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()]
                state = json.loads(snapshot.read_text("lane-map.json"))["lanes"][0]["handoff_state"]
                self.assertEqual([e["timestamp"] for e in events], [stamp] * 3)
                self.assertEqual(state["queued_at"], stamp)
        self.open_timeline()
        with patch.object(recorder, "now_iso", return_value=stamp):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "pass", "--summary", "Checked", "--artifact", "checks.md")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        event = json.loads(snapshot.read_text("timeline.jsonl").splitlines()[-1])
        trace = json.loads(snapshot.read_text("agents/orchestrator/trace.jsonl").splitlines()[-1])
        index = json.loads(snapshot.read_text("artifacts.json"))
        self.assertEqual(event, trace)
        self.assertEqual(event["timestamp"], index[0]["timestamp"])
        self.assertEqual(event["timestamp"], stamp)
    def test_adjacent_second_preserves_source_precision(self):
        events = self.source.sessions[base.QA_ID]
        events[-1]["payload"]["completed_at"] = 1789031492
        events[1]["timestamp"] = "2026-09-10T09:00:00Z"
        events[-1]["timestamp"] = "2026-09-10T09:11:33.015Z"
        before = json.dumps(events)
        result = evidence.completed_turn(self.source, base.QA_ID, base.QA_ID + "-turn", base.ROOT_ID, "qa-verifier")
        self.assertEqual(result["completed_at"].isoformat(), "2026-09-10T09:11:32+00:00")
        self.assertEqual(json.dumps(events), before)

    def test_paragraph_is_not_path(self):
        (self.run / "final.md").write_text("Run-owned changed files:\n- result.txt\n\nИсправлено оформление журнала.\n")
        self.assertEqual(evidence.changed_paths(self.run, {"run_changed_files": ["result.txt"]}), {"result.txt"})

    def test_prose_label_cannot_change_paths(self):
        final = self.run / "final.md"
        final.write_text("Run-owned changed files:\n- other.txt\n")
        first = evidence.changed_paths(self.run, {"run_changed_files": ["result.txt"]})
        final.write_text("Изменённые файлы:\n- other.txt\n")
        self.assertEqual(first, evidence.changed_paths(self.run, {"run_changed_files": ["result.txt"]}))

    def test_repeat_qa_preserves_acceptance_and_bytes(self):
        base.initialize_open_fixture(self.run)
        before = self.snapshot()
        with redirect_stdout(StringIO()):
            recorder.main(["--run-dir", str(self.run), "--role", "qa-verifier", "--lane-id", "evidence-qa",
                           "--stage", "handoff", "--status", "pass", "--summary", "Repeat",
                           "--completion-turn-id", base.QA_ID + "-turn", "--artifact", "handoffs/evidence-qa.md",
                           "--artifact", "checks.md"], session_source=self.source)
        after = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        self.assertEqual(after["verification"]["reviewer"], "evidence-reviewer")
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
