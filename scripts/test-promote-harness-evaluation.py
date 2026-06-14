#!/usr/bin/env python3
"""Fixture tests for Harness Evaluation promotion into project Evidence Records."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evidence_records import parse_notes


PROMOTE = ROOT / "scripts" / "promote-harness-evaluation.py"
ANALYZE = ROOT / "scripts" / "analyze-evidence-records.py"
VALID_CONTINUATION_RUN = ROOT / "testdata" / "golden-traces" / "valid" / "blocked-checkpoint-continuation-ship"
VALID_BLOCKED_RUN = ROOT / "testdata" / "golden-traces" / "valid" / "blocked-resolution-third-attempt-blocked"
INVALID_RUN = ROOT / "testdata" / "golden-traces" / "invalid" / "triggered-run-without-harness-evaluation"


def copy_run(root: Path, source: Path, name: str) -> Path:
    target = root / name
    shutil.copytree(source, target)
    normalize_harness_proposals(target)
    return target


def normalize_harness_proposals(run_dir: Path) -> None:
    path = run_dir / "harness-evaluation.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    for proposal in data.get("proposals", []):
        if isinstance(proposal, dict) and proposal.get("target") == "Evidence Records":
            proposal["requires_human_approval"] = False
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_blocked_learning_harness(run_dir: Path) -> None:
    data = json.loads((run_dir / "harness-evaluation.json").read_text(encoding="utf-8"))
    blocked = {
        "version": 1,
        "status": "blocked-learning",
        "learning_triggers": data["learning_triggers"],
        "source_artifacts": data["source_artifacts"],
        "findings": [],
        "proposals": [],
        "blocked_reason": "No reusable project-local practice was proven.",
        "blocked_evidence": ["checks/smoke.md"],
    }
    (run_dir / "harness-evaluation.json").write_text(
        json.dumps(blocked, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_promote(run_dir: Path, notes: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PROMOTE),
            "--run-dir",
            str(run_dir),
            "--notes",
            str(notes),
            *args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


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
        root = Path(temp_dir)
        run_dir = copy_run(root, VALID_CONTINUATION_RUN, "continuation-one")
        notes = root / "implementation-notes.md"

        assert_success(run_promote(run_dir, notes))

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


def test_missing_notes_file_creates_evidence_records_block() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        root = Path(temp_dir)
        run_dir = copy_run(root, VALID_CONTINUATION_RUN, "continuation-missing-notes")
        notes = root / "nested" / "implementation-notes.md"

        assert_success(run_promote(run_dir, notes))

        text = notes.read_text(encoding="utf-8")
        if "# Implementation Notes" not in text or "## Evidence Records" not in text:
            raise AssertionError("missing notes file did not get required headings")


def test_existing_notes_are_preserved_and_append_only() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        root = Path(temp_dir)
        run_dir = copy_run(root, VALID_CONTINUATION_RUN, "continuation-existing-notes")
        notes = root / "implementation-notes.md"
        notes.write_text(existing_notes(), encoding="utf-8")

        assert_success(run_promote(run_dir, notes))

        text = notes.read_text(encoding="utf-8")
        if "### Evidence: existing-record" not in text:
            raise AssertionError("existing record was not preserved")
        parsed = parse_notes(text)
        if len(parsed.records) != 2:
            raise AssertionError("expected existing record plus promoted record")


def test_promotion_is_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        root = Path(temp_dir)
        run_dir = copy_run(root, VALID_CONTINUATION_RUN, "continuation-idempotent")
        notes = root / "implementation-notes.md"

        assert_success(run_promote(run_dir, notes))
        first = notes.read_text(encoding="utf-8")
        assert_success(run_promote(run_dir, notes))
        second = notes.read_text(encoding="utf-8")

        if first != second:
            raise AssertionError("second promotion should not change notes")


def test_invalid_run_writes_nothing() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        root = Path(temp_dir)
        run_dir = copy_run(root, INVALID_RUN, "invalid-run")
        notes = root / "implementation-notes.md"

        assert_failure(run_promote(run_dir, notes), "validate-run failed")

        if notes.exists():
            raise AssertionError("invalid run must not create notes")


def test_blocked_learning_without_proposal_writes_nothing() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        root = Path(temp_dir)
        run_dir = copy_run(root, VALID_BLOCKED_RUN, "blocked-learning")
        write_blocked_learning_harness(run_dir)
        notes = root / "implementation-notes.md"

        assert_success(run_promote(run_dir, notes))

        if notes.exists():
            raise AssertionError("blocked learning without proposal should not create notes")


def test_third_matching_success_becomes_local_best_practice() -> None:
    with tempfile.TemporaryDirectory(prefix="harness-promotion-") as temp_dir:
        root = Path(temp_dir)
        notes = root / "implementation-notes.md"
        for name in ["continuation-a", "continuation-b", "continuation-c"]:
            run_dir = copy_run(root, VALID_CONTINUATION_RUN, name)
            assert_success(run_promote(run_dir, notes))

        report = run_analyzer(notes)
        group = report["groups"][0]
        if group["state"] != "Local Best Practice":
            raise AssertionError("third matching success should promote to Local Best Practice")
        if not group["auto_gate"]["allowed"]:
            raise AssertionError("Local Best Practice should pass auto gate")


def main() -> int:
    test_valid_run_writes_one_evidence_record()
    test_missing_notes_file_creates_evidence_records_block()
    test_existing_notes_are_preserved_and_append_only()
    test_promotion_is_idempotent()
    test_invalid_run_writes_nothing()
    test_blocked_learning_without_proposal_writes_nothing()
    test_third_matching_success_becomes_local_best_practice()
    print("PASS harness evaluation promotion fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
