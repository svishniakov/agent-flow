#!/usr/bin/env python3
"""Fixture tests for Harness Evaluation promotion into project Evidence Records."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest.mock import patch
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harness_promotion
import journal_io
from evidence_records import parse_notes
from harness_promotion import PromotionError, promote_harness_evaluation


PROMOTE = ROOT / "scripts" / "promote-harness-evaluation.py"
ANALYZE = ROOT / "scripts" / "analyze-evidence-records.py"


def load_cli(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


promotion_cli = load_cli("promotion_cli", "promote-harness-evaluation.py")
FAIL_RUNS: set[Path] = set()


def harness_data(finding_id: str = "readiness-before-workers-recovery", *, outcome: str = "success",
                 rationale: str = "Keep the practice") -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "status": "evaluated",
        "learning_triggers": ["readiness-recovery"],
        "source_artifacts": ["checks/smoke.md"],
        "findings": [
            {
                "id": finding_id,
                "type": "attempt",
                "problem_class": "verification-readiness-ordering",
                "architecture_context": [],
                "architecture_capabilities": [],
                "outcome": outcome,
                "approach": "Record readiness before worker recovery",
                "lesson": "Readiness must precede recovery",
                "reuse_when": "readiness recovery repeats",
                "do_not_reuse_when": "readiness already green",
                "evidence": ["checks/smoke.md"],
            }
        ],
        "proposals": [],
    }
    if rationale:
        data["proposals"] = [
            {
                "id": "proposal-record",
                "finding_id": finding_id,
                "type": "evidence-record",
                "target": "Evidence Records",
                "status": "proposed",
                "requires_human_approval": False,
                "rationale": rationale,
                "evidence": ["checks/smoke.md"],
            }
        ]
    return data


def make_run(project: Path, name: str, data: dict[str, Any]) -> Path:
    run_dir = project / ".agent-work" / "runs" / name
    journal_io.initialize_journal(
        run_dir,
        {"harness-evaluation.json": (json.dumps(data, indent=2) + "\n").encode()},
        source_root=project,
    )
    return run_dir


def notes_path(project: Path) -> Path:
    return project / ".agent-work" / "tasks" / "implementation-notes.md"


def validate_stub(run_dir: Path, *, snapshot=None) -> tuple[str, str]:
    if run_dir.expanduser().resolve() in FAIL_RUNS:
        raise PromotionError("validate-run failed: " + harness_promotion.validation_command_text(run_dir))
    return harness_promotion.validation_command_text(run_dir), "Synthetic verification passed"


def run_promote(run_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    stdout, stderr = StringIO(), StringIO()
    argv = [str(PROMOTE), "--run-dir", str(run_dir), *args]
    with patch.object(sys, "argv", argv), redirect_stdout(stdout), redirect_stderr(stderr):
        code = promotion_cli.main()
    return subprocess.CompletedProcess(argv, code, stdout.getvalue(), stderr.getvalue())


def run_analyzer(notes: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(ANALYZE), "--notes", str(notes), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def assert_success(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise AssertionError(f"expected success\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def assert_failure(result: subprocess.CompletedProcess[str], needle: str) -> None:
    if result.returncode == 0:
        raise AssertionError("expected failure")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"missing {needle!r}\nOutput:\n{output}")


def existing_notes() -> str:
    return """# Implementation Notes

## Evidence Records

### Evidence: existing-record

Type: Success
Problem class: existing-problem
Context: existing context
Approach: existing approach
Outcome: success
Evidence: existing evidence
Reuse when: existing reuse
Do not reuse when: existing stop
Follow-up: none
Verification: check
"""


def test_valid_run_writes_one_evidence_record() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        run_dir = make_run(project, "continuation-one", harness_data())

        assert_success(run_promote(run_dir))

        notes = notes_path(project)
        parsed = parse_notes(notes.read_text(encoding="utf-8"))
        if [record.record_id for record in parsed.records] != [
            "harness-continuation-one-readiness-before-workers-recovery"
        ]:
            raise AssertionError("promoted record id did not match run/finding")
        record = parsed.records[0]
        if record.fields["Problem class"] != "verification-readiness-ordering":
            raise AssertionError("finding problem class was not promoted")
        if "harness-evaluation.json" not in record.fields["Evidence"]:
            raise AssertionError("harness-evaluation.json missing from evidence")
        if "checks/smoke.md" not in record.fields["Evidence"]:
            raise AssertionError("finding evidence path missing from evidence")
        if "python3 scripts/validate-run.py --run-dir" not in record.fields["Verification"]:
            raise AssertionError("validation command missing from promoted record")
        for field in ["Section", "Keywords", "Provenance", "Helpful", "Harmful", "Neutral", "Active"]:
            if field not in record.fields:
                raise AssertionError(f"promoted record missing {field}")
        if record.fields["Section"] != "harness":
            raise AssertionError("promoted records should be harness records")
        if "verification-readiness-ordering" not in record.fields["Keywords"]:
            raise AssertionError("problem class keyword missing")
        if record.fields["Helpful"] != "1" or record.fields["Harmful"] != "0" or record.fields["Neutral"] != "0":
            raise AssertionError("success counters wrong")
        if record.fields["Active"] != "true":
            raise AssertionError("active flag missing")
        snapshot = journal_io.JournalSnapshot.open(run_dir)
        store = json.loads(snapshot.read_text("artifacts/memory/evidence-records.json"))
        if [row["id"] for row in store["records"]] != ["harness-continuation-one-readiness-before-workers-recovery"]:
            raise AssertionError("journal store did not retain the promoted record")


def test_missing_notes_file_creates_evidence_records_block() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        run_dir = make_run(project, "continuation-missing-notes", harness_data())

        assert_success(run_promote(run_dir))

        text = notes_path(project).read_text(encoding="utf-8")
        if "# Implementation Notes" not in text or "## Evidence Records" not in text:
            raise AssertionError("missing notes file did not get required headings")


def test_existing_notes_are_preserved_and_append_only() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        notes = notes_path(project)
        notes.parent.mkdir(parents=True)
        notes.write_text(existing_notes(), encoding="utf-8")
        run_dir = make_run(project, "continuation-existing-notes", harness_data())

        assert_success(run_promote(run_dir))

        text = notes.read_text(encoding="utf-8")
        if "### Evidence: existing-record" not in text:
            raise AssertionError("existing record was not preserved")
        parsed = parse_notes(text)
        if len(parsed.records) != 2:
            raise AssertionError("expected existing record plus promoted record")


def test_promotion_is_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        run_dir = make_run(project, "continuation-idempotent", harness_data())

        assert_success(run_promote(run_dir))
        first = notes_path(project).read_text(encoding="utf-8")
        assert_success(run_promote(run_dir))
        second = notes_path(project).read_text(encoding="utf-8")

        if first != second:
            raise AssertionError("second promotion should not change notes")
        snapshot = journal_io.JournalSnapshot.open(run_dir)
        store = json.loads(snapshot.read_text("artifacts/memory/evidence-records.json"))
        if len(store["records"]) != 1:
            raise AssertionError("second promotion should upsert, not append")


def test_dry_run_writes_nothing_and_prints_preview() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        run_dir = make_run(project, "continuation-dry-run", harness_data())

        result = run_promote(run_dir, "--dry-run")
        assert_success(result)

        if notes_path(project).exists():
            raise AssertionError("dry run must not create notes")
        snapshot = journal_io.JournalSnapshot.open(run_dir)
        if snapshot.exists("artifacts/memory/evidence-records.json"):
            raise AssertionError("dry run must not write the journal store")
        if "preview Observed:" not in result.stdout:
            raise AssertionError("dry run should print analyzer preview")
        if "helpful=1" not in result.stdout:
            raise AssertionError("dry run preview should include counters")


def test_projected_invalid_record_aborts_before_write() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        run_dir = make_run(project, "continuation-invalid-projection", harness_data())
        original = harness_promotion.promotable_records

        def invalid_records(_run_dir: Path, _data: dict[str, Any], _command_text: str) -> list[dict[str, Any]]:
            return [
                {
                    "summary": "Broken",
                    "status": "pass",
                    "id": "invalid-projection",
                    "record_type": "Success",
                    "problem_class": "p",
                    "context": "first line\nInjected: field",
                    "outcome": "success",
                    "evidence": ["e"],
                    "reuse_when": "r",
                    "do_not_reuse_when": "s",
                    "follow_up": "f",
                }
            ]

        harness_promotion.promotable_records = invalid_records
        try:
            try:
                promote_harness_evaluation(run_dir)
            except PromotionError as error:
                if "projected Evidence Records invalid" not in str(error):
                    raise AssertionError(f"wrong error: {error}")
            else:
                raise AssertionError("invalid projected record should fail")
        finally:
            harness_promotion.promotable_records = original

        if notes_path(project).exists():
            raise AssertionError("invalid projected record must not write notes")
        snapshot = journal_io.JournalSnapshot.open(run_dir)
        if snapshot.exists("artifacts/memory/evidence-records.json"):
            raise AssertionError("invalid projected record must not write the journal store")


def test_invalid_run_writes_nothing() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        run_dir = make_run(project, "invalid-run", harness_data())
        FAIL_RUNS.add(run_dir.resolve())
        try:
            assert_failure(run_promote(run_dir), "validate-run failed")
        finally:
            FAIL_RUNS.discard(run_dir.resolve())

        if notes_path(project).exists():
            raise AssertionError("invalid run must not create notes")


def test_blocked_learning_without_proposal_writes_nothing() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        run_dir = make_run(project, "blocked-learning", harness_data(rationale=""))

        assert_success(run_promote(run_dir))

        if notes_path(project).exists():
            raise AssertionError("blocked learning without proposal should not create notes")


def test_third_matching_success_becomes_local_best_practice() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        for name in ["continuation-a", "continuation-b", "continuation-c"]:
            run_dir = make_run(project, name, harness_data())
            assert_success(run_promote(run_dir))

        report = run_analyzer(notes_path(project))
        group = report["groups"][0]
        if group["state"] != "Local Best Practice":
            raise AssertionError("third matching success should promote to Local Best Practice")
        if not group["auto_gate"]["allowed"]:
            raise AssertionError("Local Best Practice should pass auto gate")


def test_promotion_requires_a_durable_journal() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        project = Path(temp_dir).resolve()
        flat = project / "flat-run"
        flat.mkdir(parents=True)
        (flat / "harness-evaluation.json").write_text(json.dumps(harness_data()), encoding="utf-8")

        assert_failure(run_promote(flat), "semantic writes require a durable journal")

        if notes_path(project).exists():
            raise AssertionError("flat run must not create notes")


def main() -> int:
    test_valid_run_writes_one_evidence_record()
    test_missing_notes_file_creates_evidence_records_block()
    test_existing_notes_are_preserved_and_append_only()
    test_promotion_is_idempotent()
    test_dry_run_writes_nothing_and_prints_preview()
    test_projected_invalid_record_aborts_before_write()
    test_invalid_run_writes_nothing()
    test_blocked_learning_without_proposal_writes_nothing()
    test_third_matching_success_becomes_local_best_practice()
    test_promotion_requires_a_durable_journal()
    print("PASS harness evaluation promotion fixture tests")
    return 0


if __name__ == "__main__":
    with patch.object(harness_promotion, "validate_run", validate_stub):
        raise SystemExit(main())
