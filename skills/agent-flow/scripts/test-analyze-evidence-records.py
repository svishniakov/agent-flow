#!/usr/bin/env python3
"""Fixture tests for Evidence Records parsing and analysis."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evidence_records import EvidenceError, analyze_notes, parse_notes


ANALYZER = ROOT / "scripts" / "analyze-evidence-records.py"


def record(
    record_id: str,
    *,
    record_type: str = "Success",
    problem_class: str = "dependency-gate-false-block",
    context: str = "completed task stayed `Status: in_progress`",
    approach: str = "normalize stale completed sections",
    outcome: str = "success",
    evidence: str = "gate no longer blocks completed work",
    reuse_when: str = "checklist complete and verification recorded",
    do_not_reuse_when: str = "verification missing",
    follow_up: str = "track next application",
    extra: dict[str, str] | None = None,
) -> str:
    fields = {
        "Type": record_type,
        "Problem class": problem_class,
        "Context": context,
        "Approach": approach,
        "Outcome": outcome,
        "Evidence": evidence,
        "Reuse when": reuse_when,
        "Do not reuse when": do_not_reuse_when,
        "Follow-up": follow_up,
    }
    fields.update(extra or {})
    body = "\n".join(f"{key}: {value}" for key, value in fields.items())
    return f"### Evidence: {record_id}\n\n{body}\n"


def notes(*records: str) -> str:
    return "# Implementation Notes\n\n## Evidence Records\n\n" + "\n".join(records)


def assert_error(name: str, text: str, needle: str) -> None:
    try:
        parse_notes(text)
    except EvidenceError as exc:
        message = str(exc)
    else:
        raise AssertionError(f"{name}: expected EvidenceError")
    if needle not in message:
        raise AssertionError(f"{name}: missing {needle!r} in {message!r}")


def group(report: dict[str, Any], problem_class: str, approach: str) -> dict[str, Any]:
    for row in report["groups"]:
        if row["problem_class"] == problem_class and row["approach"] == approach:
            return row
    raise AssertionError(f"missing group: {problem_class} / {approach}")


def write_notes(root: Path, content: str) -> Path:
    path = root / "implementation-notes.md"
    path.write_text(content, encoding="utf-8")
    return path


def run_cli(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ANALYZER), "--notes", str(path), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_parser_valid_records() -> None:
    parsed = parse_notes(notes(record("one"), record("two", approach="other approach")))
    if [item.record_id for item in parsed.records] != ["one", "two"]:
        raise AssertionError("valid records were not parsed in order")
    if parsed.records[0].fields["Problem class"] != "dependency-gate-false-block":
        raise AssertionError("required field not parsed")


def test_parser_ignores_outside_block() -> None:
    text = record("outside") + notes(record("inside")) + "\n## Other\n\n" + record("after")
    parsed = parse_notes(text)
    if [item.record_id for item in parsed.records] != ["inside"]:
        raise AssertionError("records outside Evidence Records block must be ignored")


def test_missing_block_empty() -> None:
    report = analyze_notes("# Notes\n\nNo evidence here.\n")
    if report["records"] or report["groups"] or report["errors"]:
        raise AssertionError("missing Evidence Records block should produce empty report")


def test_required_field_errors() -> None:
    assert_error("missing required", notes(record("bad").replace("Evidence: gate no longer blocks completed work\n", "")), "bad")
    assert_error("missing required line", notes(record("bad").replace("Evidence: gate no longer blocks completed work\n", "")), "missing required field: Evidence")
    assert_error("empty problem", notes(record("bad", problem_class="  ")), "empty required field: Problem class")
    assert_error("empty approach", notes(record("bad", approach="")), "empty required field: Approach")
    assert_error("empty evidence", notes(record("bad", evidence="")), "empty required field: Evidence")
    assert_error("empty reuse", notes(record("bad", reuse_when="")), "empty required field: Reuse when")
    assert_error("empty do not reuse", notes(record("bad", do_not_reuse_when="")), "empty required field: Do not reuse when")


def test_invalid_values_and_duplicates() -> None:
    assert_error("unknown type", notes(record("bad", record_type="Win")), "unknown Type")
    assert_error("unknown outcome", notes(record("bad", outcome="maybe")), "unknown Outcome")
    assert_error("duplicate id", notes(record("dup"), record("dup", approach="other")), "duplicate record id")
    assert_error("duplicate field", notes(record("bad") + "\nEvidence: repeated\n"), "repeated field")


def test_markdown_shape_edges() -> None:
    assert_error("stray text", notes(record("bad") + "\nstray text\n"), "unindented stray text")
    assert_error("heading continuation", notes(record("bad") + "\n  ## nested heading\n"), "markdown heading inside field body")
    parsed = parse_notes(notes(record("multi").replace("Evidence: gate no longer blocks completed work", "Evidence: first line\n  second line")))
    if parsed.records[0].fields["Evidence"] != "first line\nsecond line":
        raise AssertionError("indented continuation was not parsed")


def test_field_flexibility() -> None:
    text = """# Notes

## Evidence Records

### Evidence: flexible

  Approach  :  use `x:y`
Outcome: success
Evidence: Доказательство: ok
Reuse when: works
Do not reuse when: does not work
Follow-up: none
Context: non-ascii ok
Type: Success
Problem class: parser-edge
Optional field: ignored
"""
    parsed = parse_notes(text)
    record_item = parsed.records[0]
    if record_item.fields["Approach"] != "use `x:y`":
        raise AssertionError("whitespace or colon/backtick normalization failed")
    if record_item.fields["Evidence"] != "Доказательство: ok":
        raise AssertionError("non-ASCII evidence not preserved")


def test_ace_inspired_fields_are_parsed() -> None:
    enriched = record(
        "enriched",
        extra={
            "Section": "harness",
            "Keywords": "verification-readiness, continuation",
            "Provenance": "run=/tmp/run; finding=readiness; triggers=continuation; evidence=checks/smoke.md",
            "Helpful": "2",
            "Harmful": "0",
            "Neutral": "1",
            "Active": "true",
        },
    )
    parsed = parse_notes(notes(enriched))
    item = parsed.records[0]
    report = analyze_notes(notes(enriched))
    record_json = report["records"][0]
    group_json = group(report, "dependency-gate-false-block", "normalize stale completed sections")

    if item.fields["Section"] != "harness":
        raise AssertionError("section field missing")
    if record_json["keywords"] != ["verification-readiness", "continuation"]:
        raise AssertionError("keywords were not parsed")
    if record_json["helpful"] != 2 or record_json["harmful"] != 0 or record_json["neutral"] != 1:
        raise AssertionError("explicit counters were not parsed")
    if record_json["active"] is not True:
        raise AssertionError("active flag was not parsed")
    if group_json["section_counts"] != {"harness": 1}:
        raise AssertionError("section summary wrong")
    if group_json["keywords"] != ["verification-readiness", "continuation"]:
        raise AssertionError("keyword summary wrong")


def test_old_records_get_derived_defaults() -> None:
    report = analyze_notes(
        notes(
            record("success", outcome="success"),
            record("failure", record_type="Failure", outcome="failure"),
            record("unknown", record_type="Attempt", outcome="unknown"),
        )
    )
    values = {row["id"]: row for row in report["records"]}
    if values["success"]["helpful"] != 1 or values["success"]["harmful"] != 0:
        raise AssertionError("success default counters wrong")
    if values["failure"]["harmful"] != 1:
        raise AssertionError("failure default counter wrong")
    if values["unknown"]["neutral"] != 1:
        raise AssertionError("unknown default counter wrong")
    if values["success"]["active"] is not True or values["success"]["keywords"]:
        raise AssertionError("old record defaults wrong")


def test_invalid_ace_inspired_fields() -> None:
    assert_error("invalid section", notes(record("bad", extra={"Section": "runtime"})), "Section must be one of")
    assert_error("invalid keywords", notes(record("bad", extra={"Keywords": "Good Token"})), "Keywords must be")
    assert_error("empty keywords", notes(record("bad", extra={"Keywords": " , "})), "Keywords must include")
    assert_error("invalid counter", notes(record("bad", extra={"Helpful": "-1"})), "Helpful must be a non-negative integer")
    assert_error("invalid active", notes(record("bad", extra={"Active": "maybe"})), "Active must be true or false")


def test_inactive_records_are_excluded_from_classification() -> None:
    report = analyze_notes(
        notes(
            record("s1", extra={"Verification": "check", "Active": "false"}),
            record("s2", extra={"Verification": "check", "Active": "false"}),
            record("s3", extra={"Verification": "check", "Active": "false"}),
        )
    )
    row = group(report, "dependency-gate-false-block", "normalize stale completed sections")
    if row["state"] != "Inactive":
        raise AssertionError("inactive group should not classify as practice")
    if row["attempts"] != 0 or row["inactive"] != 3:
        raise AssertionError("inactive counts wrong")
    if row["auto_gate"]["allowed"]:
        raise AssertionError("inactive group must not pass auto gate")


def test_harmful_counters_block_auto_gate() -> None:
    report = analyze_notes(
        notes(
            record("s1", extra={"Verification": "check", "Helpful": "1", "Harmful": "1"}),
            record("s2", extra={"Verification": "check", "Helpful": "1", "Harmful": "1"}),
            record("s3", extra={"Verification": "check", "Helpful": "1", "Harmful": "1"}),
        )
    )
    gate = group(report, "dependency-gate-false-block", "normalize stale completed sections")["auto_gate"]
    if gate["allowed"]:
        raise AssertionError("harmful counters should block auto gate")
    if "harmful evidence is not below helpful evidence" not in gate["blockers"]:
        raise AssertionError("harmful blocker missing")


def test_aggregation_counts_and_boundaries() -> None:
    report = analyze_notes(
        notes(
            record("s1", outcome="success"),
            record("f1", outcome="failure"),
            record("r1", outcome="regression"),
            record("j1", outcome="rejected"),
            record("u1", outcome="unknown"),
            record("same-approach-other-problem", problem_class="other-problem"),
            record("same-problem-other-approach", approach="other approach"),
            record("arch-a", record_type="Architecture Attempt", outcome="success"),
            record("arch-f", record_type="Architecture Failure", outcome="failure"),
            record("orch-f", record_type="Orchestration Failure", outcome="failure"),
        )
    )
    main = group(report, "dependency-gate-false-block", "normalize stale completed sections")
    for key, value in {
        "attempts": 8,
        "success": 2,
        "failure": 3,
        "regression": 1,
        "rejected": 1,
        "unknown": 1,
        "architecture_attempts": 1,
        "architecture_failures": 1,
        "orchestration_failures": 1,
        "incomplete": 1,
    }.items():
        if main[key] != value:
            raise AssertionError(f"{key} expected {value}, got {main[key]}")
    if not group(report, "other-problem", "normalize stale completed sections"):
        raise AssertionError("different problem class not separated")
    if not group(report, "dependency-gate-false-block", "other approach"):
        raise AssertionError("different approach not separated")


def test_practice_states() -> None:
    observed = analyze_notes(notes(record("s1")))
    if group(observed, "dependency-gate-false-block", "normalize stale completed sections")["state"] != "Observed":
        raise AssertionError("1 success should be Observed")

    candidate = analyze_notes(notes(record("s1"), record("s2")))
    if group(candidate, "dependency-gate-false-block", "normalize stale completed sections")["state"] != "Candidate Practice":
        raise AssertionError("2 successes should be Candidate Practice")

    best = analyze_notes(notes(record("s1"), record("s2"), record("s3", extra={"Verification": "python3 scripts/check-all.py"})))
    if group(best, "dependency-gate-false-block", "normalize stale completed sections")["state"] != "Local Best Practice":
        raise AssertionError("3 clean successes should be Local Best Practice")

    unknown_does_not_demote = analyze_notes(notes(
        record("s1", extra={"Verification": "check"}),
        record("s2", extra={"Verification": "check"}),
        record("s3", extra={"Verification": "check"}),
        record("u1", outcome="unknown"),
    ))
    unknown_group = group(unknown_does_not_demote, "dependency-gate-false-block", "normalize stale completed sections")
    if unknown_group["state"] != "Local Best Practice" or unknown_group["incomplete"] != 1:
        raise AssertionError("unknown outcomes should be reported incomplete without blocking promotion")

    with_failure = analyze_notes(notes(record("s1"), record("s2"), record("s3"), record("f1", outcome="failure")))
    if group(with_failure, "dependency-gate-false-block", "normalize stale completed sections")["state"] != "Candidate Practice":
        raise AssertionError("single failure should demote to Candidate Practice")

    anti = analyze_notes(notes(record("f1", outcome="failure"), record("j1", outcome="rejected")))
    if group(anti, "dependency-gate-false-block", "normalize stale completed sections")["state"] != "Anti-pattern":
        raise AssertionError("2 bad outcomes should be Anti-pattern")

    unrelated = analyze_notes(notes(record("f1", outcome="failure"), record("j1", outcome="rejected"), record("s1", problem_class="other-problem"), record("s2", problem_class="other-problem"), record("s3", problem_class="other-problem")))
    if group(unrelated, "dependency-gate-false-block", "normalize stale completed sections")["state"] != "Anti-pattern":
        raise AssertionError("unrelated successes must not rescue anti-pattern")


def test_promotion_guards_and_auto_gate() -> None:
    unknown_reuse = analyze_notes(notes(record("s1", reuse_when="UNKNOWN"), record("s2", reuse_when="UNKNOWN"), record("s3", reuse_when="UNKNOWN")))
    main = group(unknown_reuse, "dependency-gate-false-block", "normalize stale completed sections")
    if main["state"] != "Candidate Practice" or main["promotion_blockers"] != ["missing reuse boundaries"]:
        raise AssertionError("UNKNOWN reuse boundaries must block promotion")

    best = analyze_notes(notes(
        record("s1", extra={"Verification": "check"}),
        record("s2", extra={"Verification": "check"}),
        record("s3", extra={"Verification": "check"}),
    ))
    if not group(best, "dependency-gate-false-block", "normalize stale completed sections")["auto_gate"]["allowed"]:
        raise AssertionError("Local Best Practice with verification should be auto-applicable")
    if not group(best, "dependency-gate-false-block", "normalize stale completed sections")["auto_gate"]["attempt_record_required"]:
        raise AssertionError("auto-apply should require a new Attempt record")

    for state_name, report in {
        "Observed": analyze_notes(notes(record("s1", extra={"Verification": "check"}))),
        "Candidate Practice": analyze_notes(notes(record("s1", extra={"Verification": "check"}), record("s2", extra={"Verification": "check"}))),
    }.items():
        if group(report, "dependency-gate-false-block", "normalize stale completed sections")["auto_gate"]["allowed"]:
            raise AssertionError(f"{state_name} must not auto-apply")

    blockers = {
        "do-not-reuse": {"Do not reuse when hit": "true", "Verification": "check"},
        "uncertain-context": {"Context match": "uncertain", "Verification": "check"},
        "external-write": {"External action": "commit", "Verification": "check"},
    }
    for name, extra in blockers.items():
        report = analyze_notes(notes(record("s1", extra=extra), record("s2", extra=extra), record("s3", extra=extra)))
        gate = group(report, "dependency-gate-false-block", "normalize stale completed sections")["auto_gate"]
        if gate["allowed"] or not gate["blockers"]:
            raise AssertionError(f"{name} should block auto gate")

    no_verification = analyze_notes(notes(record("s1"), record("s2"), record("s3")))
    if group(no_verification, "dependency-gate-false-block", "normalize stale completed sections")["auto_gate"]["allowed"]:
        raise AssertionError("auto gate requires explicit verification")


def test_auto_apply_outcomes() -> None:
    failed_auto = analyze_notes(notes(
        record("s1", extra={"Verification": "check"}),
        record("s2", extra={"Verification": "check"}),
        record("s3", extra={"Verification": "check"}),
        record("f1", record_type="Attempt", outcome="failure", extra={"Applied by": "automatic-local-best-practice"}),
    ))
    failed_group = group(failed_auto, "dependency-gate-false-block", "normalize stale completed sections")
    if failed_group["state"] != "Candidate Practice":
        raise AssertionError("failed auto apply should demote practice")

    regressed_auto = analyze_notes(notes(
        record("s1", extra={"Verification": "check"}),
        record("s2", extra={"Verification": "check"}),
        record("s3", extra={"Verification": "check"}),
        record("r1", record_type="Regression", outcome="regression", extra={"Applied by": "automatic-local-best-practice", "Severity": "serious"}),
    ))
    gate = group(regressed_auto, "dependency-gate-false-block", "normalize stale completed sections")["auto_gate"]
    if not gate["frozen"] or "architecture approval required" not in gate["blockers"]:
        raise AssertionError("regression after auto apply must freeze practice")


def test_cli_modes() -> None:
    with tempfile.TemporaryDirectory(prefix="evidence-cli-") as temp_dir:
        root = Path(temp_dir)
        missing = root / "missing.md"
        missing_result = run_cli(missing)
        if missing_result.returncode != 0 or "0 records" not in missing_result.stdout:
            raise AssertionError("missing notes file should be empty report")
        strict_missing = run_cli(missing, "--fail-on-invalid")
        if strict_missing.returncode == 0:
            raise AssertionError("missing notes file should fail in strict mode")

        empty = write_notes(root, "")
        if run_cli(empty).returncode != 0:
            raise AssertionError("empty file should not fail")
        strict_no_block = run_cli(empty, "--fail-on-invalid")
        if strict_no_block.returncode == 0 or "missing ## Evidence Records block" not in strict_no_block.stderr:
            raise AssertionError("missing Evidence Records block should fail in strict mode")

        valid = write_notes(root, notes(record("s1"), record("s2"), record("s3", extra={"Verification": "check"})))
        before = valid.read_text(encoding="utf-8")
        json_result = run_cli(valid, "--json")
        if json_result.returncode != 0:
            raise AssertionError(json_result.stderr)
        data = json.loads(json_result.stdout)
        if data["groups"][0]["state"] != "Local Best Practice":
            raise AssertionError("--json output did not include expected state")
        if valid.read_text(encoding="utf-8") != before:
            raise AssertionError("analyzer must not edit notes file")

        threshold = run_cli(valid, "--json", "--min-successes", "4")
        if json.loads(threshold.stdout)["groups"][0]["state"] != "Candidate Practice":
            raise AssertionError("--min-successes should change promotion threshold")

        invalid = write_notes(root, notes(record("bad", outcome="maybe")))
        invalid_result = run_cli(invalid, "--fail-on-invalid")
        if invalid_result.returncode == 0 or "unknown Outcome" not in invalid_result.stderr:
            raise AssertionError("invalid markdown should fail in strict mode")


def test_large_input_deterministic() -> None:
    records = [
        record(f"s{index}", problem_class=f"problem-{index % 7}", approach=f"approach-{index % 5}")
        for index in range(120)
    ]
    first = analyze_notes(notes(*records))
    second = analyze_notes(notes(*records))
    if json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True):
        raise AssertionError("large notes report must be deterministic")


def main() -> int:
    test_parser_valid_records()
    test_parser_ignores_outside_block()
    test_missing_block_empty()
    test_required_field_errors()
    test_invalid_values_and_duplicates()
    test_markdown_shape_edges()
    test_field_flexibility()
    test_ace_inspired_fields_are_parsed()
    test_old_records_get_derived_defaults()
    test_invalid_ace_inspired_fields()
    test_inactive_records_are_excluded_from_classification()
    test_harmful_counters_block_auto_gate()
    test_aggregation_counts_and_boundaries()
    test_practice_states()
    test_promotion_guards_and_auto_gate()
    test_auto_apply_outcomes()
    test_cli_modes()
    test_large_input_deterministic()
    print("PASS evidence record analyzer fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
