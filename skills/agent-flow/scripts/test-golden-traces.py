#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

from journal_io import JournalError
from journal_lifecycle import finalize

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "testdata/golden-traces"
spec = importlib.util.spec_from_file_location("golden_evidence", ROOT / "scripts/test-verification-evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def check_case(case):
    with tempfile.TemporaryDirectory(prefix="agent-flow-golden-") as directory:
        fixture = evidence.Fixture(directory)
        if case["scenario"] != "failed-partial":
            fixture.completed()
        if case["scenario"] == "stale-qa":
            fixture.product.write_text("answer = 3\n")
        arguments = {"identifier": "golden-final", "status": case["status"],
                     "summary": case["summary"], "source": fixture.source}
        if case["status"] == "failed":
            arguments.update(reason=case["reason"], unverified=case["unverified"])
        before = fixture.snapshot().revision
        try:
            finalize(fixture.run, **arguments)
        except JournalError as error:
            if not case.get("expected_error") or case["expected_error"] not in str(error):
                raise AssertionError(f"{case['scenario']}: unexpected refusal: {error}") from error
            snapshot = fixture.snapshot()
            assert snapshot.revision == before, "rejected final changed journal revision"
            assert not snapshot.exists("final.json"), "rejected final published a document"
        else:
            assert not case.get("expected_error"), "stale result was accepted"
            snapshot = fixture.snapshot()
            final = snapshot.json("final.json")
            assert final["status"] == case["status"]
            assert final["summary"] == case["summary"]
            if case["status"] == "completed":
                assert final["reports"] == ["reports/author.json", "reports/qa.json", "reports/review.json"]
            else:
                assert final["reason"] == case["reason"]
                assert final["unverified"] == case["unverified"]
            revision = snapshot.revision
            finalize(fixture.run, **arguments)
            assert fixture.snapshot().revision == revision, "technical retry published twice"
        assert not (fixture.root / ".agent-work/workspaces").exists(), "workflow copied the checkout"
        print(f"PASS {case['scenario']}")


def main():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    cases = manifest["cases"]
    assert isinstance(cases, list) and cases, "golden cases required"
    scenarios = set()
    for item in cases:
        path = FIXTURES / item["path"]
        assert not path.is_symlink() and path.resolve().is_relative_to(FIXTURES.resolve()) and path.is_file(), "unsafe golden fixture"
        case = json.loads(path.read_text())
        assert case["scenario"] not in scenarios, "duplicate golden scenario"
        scenarios.add(case["scenario"])
        check_case(case)
    assert scenarios == {"completed", "stale-qa", "failed-partial"}, "ordinary workflow coverage missing"
    print("PASS golden trace runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
