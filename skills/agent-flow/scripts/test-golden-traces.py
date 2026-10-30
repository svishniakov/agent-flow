#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

from journal_io import JournalError, encode_json, transact
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
            fixture.reviewed()
            fixture.save('qa', {'status': 'completed', 'outcome': 'passed', 'summary': case['summary'],
                'evidence': [fixture.ref(evidence.QA_ID)],
                'criteria': {'ac-1': {'status': 'passed', 'evidence': [fixture.ref(evidence.QA_ID)]}}})
        if case["scenario"] == "other-chat-change":
            (fixture.root / "other-chat.md").write_text("Another task completed\n")
        arguments = {"identifier": "golden-final", "report_id": "qa", "source": fixture.source}
        if case["status"] == "failed":
            assignment = {**fixture.known['author'], 'id': 'stop', 'kind': 'analysis', 'allowed_paths': []}
            fixture.known['stop'] = assignment
            transact(fixture.run, 'stop-assignment', {}, lambda current: (
                {'assignments/stop.json': encode_json(assignment).encode()}, {}))
            fixture.source.sessions[evidence.ROOT_ID].extend([
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'inspection', 'name': 'exec_command', 'input': '{"cmd":"inspect current result"}'}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'inspection', 'output': '{"exit_code":0,"output":"Stopped state inspected"}'}},
            ])
            fixture.save('stop', {'status': 'interrupted', 'outcome': 'unverified', 'summary': case['summary'],
                'reason': case['reason'], 'unverified': case['unverified'], 'evidence': [{**fixture.ref(evidence.ROOT_ID), 'call_id': 'inspection'}]})
            arguments['report_id'] = 'stop'
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
            assert not case.get("expected_error"), "invalid result was accepted"
            snapshot = fixture.snapshot()
            final = snapshot.json("final.json")
            assert final["status"] == ('interrupted' if case['status'] == 'failed' else 'completed')
            assert final["summary"] == case["summary"]
            if case["status"] == "completed":
                assert final["report_id"] == 'qa'
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
    assert scenarios == {"completed", "other-chat-change", "failed-partial"}, "ordinary workflow coverage missing"
    print("PASS golden trace runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
