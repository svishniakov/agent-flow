"""Focused model-settings scenarios, also run by test-golden-traces.py."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from model_settings import CONTEXT_FIELDS, validate_model_settings
from model_settings_fixtures import ROOT_SESSION, add_assignment, fixture_id, seed_model_settings, stage_test_observations, stamp, write_json, write_jsonl


def load(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def make_run(path):
    path.mkdir()
    seed_model_settings(path)
    data = load(path / "model-settings.json")
    records, timeline = [], []
    for number, aid in enumerate(("reviewer-plan", "reviewer-final"), 1):
        sid = fixture_id(aid)
        data["assignments"].append(add_assignment(path, aid, "reviewer", sid, second=number * 5))
        handoff = "handoff-" + aid + ".md"
        identity = {"assignment_id": aid, "role": "reviewer", "codex_thread_id": sid, "parent_thread_id": ROOT_SESSION}
        records.append({**identity, "handoff": handoff})
        (path / handoff).write_text("\n".join(f"{key}: {value}" for key, value in identity.items()))
        timeline.extend([{**identity, "stage": stage, "status": "pass", "execution_mode": "subagent", "artifacts": [handoff]} for stage in ("spawned", "handoff")])
    (path / "agents" / "reviewer").mkdir(parents=True)
    write_jsonl(path / "agents" / "reviewer" / "trace.jsonl", timeline)
    write_jsonl(path / "timeline.jsonl", timeline)
    write_json(path / "delegation-summary.json", {"subagents": records})
    write_json(path / "model-settings.json", data)
    return data


def escalate(path, assignment, *, second=20):
    sid = assignment["sessions"][0]["session_id"]
    event = {"kind": "escalation", "session_id": sid, "at": stamp(second), "attempt": 1,
             "from_effort": assignment["baseline_effort"], "to_effort": assignment["ceiling_effort"],
             "trigger": "architecture-risk" if assignment["role"] == "root" else "qa-critical", "reason": "Checked requirements; coordination requires higher effort."}
    source = {"captured_at": event["at"], "method": "reasoning/escalation",
              "params": {key: value for key, value in event.items() if key not in {"kind", "at"}}}
    source["params"]["assignment_id"] = assignment["assignment_id"]
    write_jsonl(path / "decision.jsonl", [source])
    event["evidence"] = {"path": "decision.jsonl", "line": 1}
    assignment["events"].append(event)


def invoke(path, assignment, *, second=21, effort=None):
    session = assignment["sessions"][-1]
    sid = session["session_id"]
    effort = effort or assignment["ceiling_effort"]
    turn_id = f"fixture-turn-{second}"
    rollout_path = path / session["session_evidence"]["path"]
    rollout = lines(rollout_path)
    rollout.append({"timestamp": stamp(second + 1), "type": "turn_context",
                    "payload": {"turn_id": turn_id, "model": assignment["model"], "effort": effort}})
    write_jsonl(rollout_path, rollout)
    tool_ref = session["launch_evidence"].get("path") or f"model-evidence/{assignment['assignment_id']}-tool.jsonl"
    tool_path = path / tool_ref
    tool = lines(tool_path)
    tool.extend([{"captured_at": stamp(second), "method": "turn/start", "params": {"threadId": sid, "effort": effort}, "result": {"turn": {"id": turn_id}}},
                 {"captured_at": stamp(second + 2), "method": "turn/completed", "params": {"threadId": sid, "turn": {"id": turn_id, "status": "completed"}}}])
    write_jsonl(tool_path, tool)
    assignment["events"].append({"kind": "invocation", "session_id": sid, "turn_id": turn_id, "at": stamp(second), "attempt": 1, "effort": effort,
        "evidence": {"path": session["session_evidence"]["path"], "line": len(rollout)},
        "request_evidence": {"path": tool_ref, "line": len(tool)-1},
        "completion_evidence": {"path": tool_ref, "line": len(tool)}})


def run_model_settings_tests():
    count = 0
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)

        def check(name, mutate=lambda path, data: None, error=None):
            nonlocal count
            path = root / str(count)
            data = make_run(path)
            mutate(path, data)
            write_json(path / "model-settings.json", data)
            codex_home = stage_test_observations(path)
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                errors = validate_model_settings(path)
            if error is None:
                assert not errors, (name, errors)
            else:
                assert any(error in item for item in errors), (name, error, errors)
            count += 1

        check("two independent reviewers")
        check("missing root", lambda p, d: d["assignments"].pop(0), "root assignment missing")
        for field, value in (("model", "gpt-5.6-sol"), ("service_tier", "priority"), ("baseline_effort", "medium"), ("ceiling_effort", "max")):
            check("immutable " + field, lambda p, d, f=field, v=value: d["assignments"][0].update({f: v}), "immutable " + field)
        check("fake task ID", lambda p, d: d["assignments"][1]["sessions"][0].update(session_id="reviewer-plan"), "differs from launch source")
        check("wrong parent", lambda p, d: d["assignments"][1]["sessions"][0].update(parent_session_id="other-root"), "differs from launch source")
        check("missing session observation", lambda p, d: d["assignments"][0]["sessions"][0].pop("session_evidence"), "missing source evidence")
        check("missing launch source", lambda p, d: (p / "model-evidence/root-tool.jsonl").unlink(), "source evidence unavailable")
        def fake_launch(path, data):
            source = path / "model-evidence/root-tool.jsonl"
            records = lines(source)
            records[0]["result"]["thread"]["id"] = "root-task-name"
            write_jsonl(source, records)
        check("task name in launch source is not real ID", fake_launch, "UUIDv7")
        def self_report(path, data):
            source = path / "model-evidence/root-tool.jsonl"
            records = lines(source)
            records[0]["method"] = "model/self_report"
            write_jsonl(source, records)
        check("model self report is not launch proof", self_report, "thread/start tool result")
        def child_as_root(path, data, source=None):
            rollout = path / "model-evidence/root-rollout.jsonl"
            records = lines(rollout)
            records[0]["payload"]["source"] = source or {"subagent": {"thread_spawn": {"parent_thread_id": data["assignments"][1]["sessions"][0]["session_id"], "agent_path": "/root/fake-root"}}}
            write_jsonl(rollout, records)
        check("child session cannot satisfy root with forged parent-null capture", child_as_root, "non-subagent client session source")
        check("subagent source string cannot satisfy root", lambda p, d: child_as_root(p, d, "subagent"), "non-subagent client session source")
        def default_tier(path, data):
            source = path / "model-evidence/root-tool.jsonl"
            records = lines(source)
            records[0]["result"]["serviceTier"] = "default"
            write_jsonl(source, records)
        check("observed default tier means no override", default_tier)
        def repeated_context_drift(path, data):
            source = path / "model-evidence/root-rollout.jsonl"
            records = lines(source)
            drift = json.loads(json.dumps(records[-1]))
            drift["payload"]["effort"] = "medium"
            write_jsonl(source, records + [drift])
        check("repeated context cannot conceal drift", repeated_context_drift, "changed settings within recorded turn")
        check("missing completion", lambda p, d: d["assignments"][0]["events"][0].pop("completion_evidence"), "missing source evidence")
        check("swapped reviewer session", lambda p, d: d["assignments"][2]["sessions"][0].update(session_id=d["assignments"][1]["sessions"][0]["session_id"]), "session_id missing or reused")

        def trace_change(path, change):
            events = lines(path / "timeline.jsonl")
            change(events)
            write_jsonl(path / "timeline.jsonl", events)
            write_jsonl(path / "agents/reviewer/trace.jsonl", events)

        check("spawn typo", lambda p, d: trace_change(p, lambda e: e[0].update(stage="spawn")), "stage=spawn is invalid")
        check("second reviewer missing spawn", lambda p, d: trace_change(p, lambda e: e.pop(2)), "initial spawned event")
        check("second reviewer missing terminal", lambda p, d: trace_change(p, lambda e: e.pop()), "missing terminal handoff")
        check("changed later ID", lambda p, d: trace_change(p, lambda e: e[1].update(codex_thread_id="fake")), "differs from observed launch")
        check("changed handoff ID", lambda p, d: (p / "handoff-reviewer-final.md").write_text("codex_thread_id: fake"), "missing/mismatched codex_thread_id")
        check("timeline trace mismatch", lambda p, d: write_jsonl(p / "timeline.jsonl", []), "differs from timeline")

        def raised(path, data):
            escalate(path, data["assignments"][0])
            invoke(path, data["assignments"][0])
            invoke(path, data["assignments"][0], second=25)

        check("same-session escalation and continuation", raised)
        def omitted_followup(path, data):
            raised(path, data)
            source = path / "model-evidence/root-tool.jsonl"
            tool = lines(source)
            tool[-2]["params"].pop("effort")
            write_jsonl(source, tool)
        check("ordinary followup inherits elevated effort", omitted_followup)
        def missing_transition_effort(path, data):
            raised(path, data)
            source = path / "model-evidence/root-tool.jsonl"
            tool = lines(source)
            tool[3]["params"].pop("effort")
            write_jsonl(source, tool)
        check("transition cannot omit effort", missing_transition_effort, "explicit effort request")
        def interrupted_then_continue(path, data):
            source = path / "model-evidence/root-tool.jsonl"
            tool = lines(source)
            tool[2]["params"]["turn"]["status"] = "interrupted"
            write_jsonl(source, tool)
            invoke(path, data["assignments"][0], effort="high")
        check("interrupted turn followed by success", interrupted_then_continue)
        check("omitted invocation cannot conceal settings", lambda p, d: (raised(p, d), d["assignments"][0]["events"].pop()), "unrecorded or mismatched invocations")
        check("unapproved increase", lambda p, d: invoke(p, d["assignments"][0]), "observed effort differs")
        check("reset attempt", lambda p, d: (raised(p, d), d["assignments"][0]["events"][-1].update(attempt=0)), "attempt reset")
        check("illegal trigger", lambda p, d: (raised(p, d), d["assignments"][0]["events"][1].update(trigger="retry")), "permitted trigger")
        check("ceiling exceeded", lambda p, d: (raised(p, d), d["assignments"][0]["events"][1].update(to_effort="max")), "illegal escalation")
        check("backdated reason", lambda p, d: (raised(p, d), d["assignments"][0]["events"][1].update(at=stamp(0))), "backdated")
        check("downshift", lambda p, d: (raised(p, d), invoke(p, d["assignments"][0], second=30, effort="high")), "downshift forbidden")
        check("changed invocation model", lambda p, d: change_context_model(p), "observed model")

        def successor(path, data):
            assignment = data["assignments"][0]
            escalate(path, assignment)
            following = add_assignment(path, "root-next", "root", fixture_id("root-next"), parent=None, second=22)
            session = following["sessions"][0]
            session.update(predecessor_session_id=ROOT_SESSION, context_snapshot="context-next.json", stop_evidence={"path": "stop.jsonl", "line": 1})
            snapshot = {key: "Retained fixture state" for key in CONTEXT_FIELDS}
            snapshot.update(assignment_id="root", predecessor_session_id=ROOT_SESSION, attempt=1, scope=assignment["scope"], evidence=[assignment["events"][0]["evidence"]])
            write_json(path / "context-next.json", snapshot)
            write_jsonl(path / "stop.jsonl", [{"captured_at": stamp(21), "method": "thread/archive", "params": {"threadId": ROOT_SESSION}, "result": {}}])
            launch_path = path / session["launch_evidence"]["path"]
            tool = lines(launch_path)
            tool[0]["result"]["reasoningEffort"] = "xhigh"
            tool[1]["params"]["effort"] = "xhigh"
            write_jsonl(launch_path, tool)
            rollout_path = path / session["session_evidence"]["path"]
            rollout = lines(rollout_path)
            rollout[1]["payload"]["effort"] = "xhigh"
            write_jsonl(rollout_path, rollout)
            following["events"][0]["effort"] = "xhigh"
            assignment["sessions"].append(session)
            assignment["events"].extend(following["events"])

        check("linked successor retains context and attempt", successor)
        check("successor without stop", lambda p, d: (successor(p, d), d["assignments"][0]["sessions"][1].pop("stop_evidence")), "missing source evidence")
        check("successor incomplete context", lambda p, d: (successor(p, d), write_json(p / "context-next.json", {})), "complete context snapshot")
        check("successor attempt reset", lambda p, d: (successor(p, d), reset_snapshot_attempt(p)), "reset attempt")
        def terminal_successor(path, data, late=False):
            successor(path, data)
            assignment = data["assignments"][0]
            source_path = assignment["sessions"][0]["session_evidence"]["path"]
            source = lines(path / source_path)
            source.append({"type": "event_msg", "timestamp": stamp(3), "payload": {"type": "task_complete", "turn_id": assignment["events"][0]["turn_id"]}})
            pointer = {"path": source_path, "line": len(source)}
            assignment["events"][0]["completion_evidence"] = pointer
            assignment["sessions"][1]["stop_evidence"] = pointer
            if late:
                source.append({"type": "event_msg", "timestamp": stamp(4), "payload": {"type": "task_started", "turn_id": "late-turn"}})
            write_jsonl(path / source_path, source)
        check("terminal client record stops predecessor", terminal_successor)
        check("terminal predecessor cannot execute later", lambda p, d: terminal_successor(p, d, late=True), "execution after terminal stop")
        check("missing baseline settings", lambda p, d: d.clear(), "schema_version")

        def native(path, data):
            assignment = data["assignments"][1]
            session = assignment["sessions"][0]
            invocation = assignment["events"][0]
            root_source = "model-evidence/root-rollout.jsonl"
            records = lines(path / root_source)
            records.extend([
                {"timestamp": stamp(5), "type": "response_item", "payload": {"type": "function_call", "namespace": "collaboration", "name": "spawn_agent", "call_id": "call-fixture", "arguments": json.dumps({"task_name": assignment["assignment_id"], "model": assignment["model"], "reasoning_effort": "high"})}},
                {"timestamp": stamp(5), "type": "event_msg", "payload": {"type": "item_completed", "thread_id": ROOT_SESSION, "item": {"type": "SubAgentActivity", "kind": "started", "id": "call-fixture", "agent_thread_id": session["session_id"], "agent_path": "/root/reviewer-plan"}}},
                {"timestamp": stamp(5), "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-fixture", "output": json.dumps({"task_name": "/root/reviewer-plan"})}},
            ])
            write_jsonl(path / root_source, records)
            child_source = session["session_evidence"]["path"]
            child = lines(path / child_source)
            child[0]["payload"]["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": ROOT_SESSION, "agent_path": "/root/reviewer-plan"}}}
            child.extend([
                {"timestamp": stamp(6), "type": "event_msg", "payload": {"type": "task_started", "turn_id": invocation["turn_id"]}},
                {"timestamp": stamp(8), "type": "event_msg", "payload": {"type": "task_complete", "turn_id": invocation["turn_id"]}},
            ])
            write_jsonl(path / child_source, child)
            session["launch_evidence"] = {"kind": "native", "call": {"path": root_source, "line": 3}, "activity": {"path": root_source, "line": 4}, "result": {"path": root_source, "line": 5}, "session": session["session_evidence"], "context": invocation["evidence"]}
            invocation["request_evidence"] = {"kind": "native", "launch": session["launch_evidence"], "started": {"path": child_source, "line": 3}}
            invocation["completion_evidence"] = {"path": child_source, "line": 4}
        check("native client activity links real ID", native)
        check("native fake activity", lambda p, d: (native(p, d), d["assignments"][1]["sessions"][0]["launch_evidence"].update(activity={"path": "model-evidence/root-rollout.jsonl", "line": 5})), "matching client SubAgentActivity")
        check("native omitted reviewer assignment", lambda p, d: (native(p, d), d["assignments"].pop(1)), "native launched child assignment omitted")
        check("native assignment cannot be renamed", lambda p, d: (native(p, d), d["assignments"][1].update(assignment_id="another-reviewer")), "actual native spawn task_name")
        def native_successor(path, data):
            native(path, data)
            terminal_successor(path, data)
            root_assignment = data["assignments"][0]
            root_decision = root_assignment["events"][1]
            (path / "decision-root.jsonl").write_text((path / "decision.jsonl").read_text())
            root_decision["evidence"] = {"path": "decision-root.jsonl", "line": 1}
            next_parent = root_assignment["sessions"][1]["session_id"]
            root_tool = path / root_assignment["sessions"][1]["launch_evidence"]["path"]
            records = lines(root_tool)
            records[2]["captured_at"] = stamp(35)
            write_jsonl(root_tool, records)

            assignment = data["assignments"][1]
            predecessor = assignment["sessions"][0]["session_id"]
            following = add_assignment(path, "reviewer-plan-next", "reviewer", fixture_id("reviewer-plan-next"), parent=next_parent, second=30)
            session = following["sessions"][0]
            session.update(predecessor_session_id=predecessor, context_snapshot="reviewer-next-context.json", stop_evidence=assignment["events"][0]["completion_evidence"])
            snapshot = {key: "Preserved reviewer acceptance and state" for key in CONTEXT_FIELDS}
            snapshot.update(assignment_id=assignment["assignment_id"], predecessor_session_id=predecessor, attempt=1, scope=assignment["scope"], evidence=[assignment["events"][0]["evidence"]])
            write_json(path / "reviewer-next-context.json", snapshot)
            escalate(path, assignment, second=29)
            assignment["events"][-1]["session_id"] = session["session_id"]
            decision = lines(path / "decision.jsonl")
            decision[0]["params"]["parent_session_id"] = next_parent
            write_jsonl(path / "decision.jsonl", decision)
            source = path / session["launch_evidence"]["path"]
            records = lines(source)
            records[0]["result"]["reasoningEffort"] = "xhigh"
            records[1]["params"]["effort"] = "xhigh"
            write_jsonl(source, records)
            source = path / session["session_evidence"]["path"]
            records = lines(source)
            records[1]["payload"]["effort"] = "xhigh"
            write_jsonl(source, records)
            following["events"][0]["effort"] = "xhigh"
            assignment["sessions"].append(session)
            assignment["events"].extend(following["events"])
            handoff = "handoff-reviewer-successor.md"
            identity = {"assignment_id": assignment["assignment_id"], "role": "reviewer", "codex_thread_id": session["session_id"], "parent_thread_id": next_parent}
            (path / handoff).write_text("\n".join(f"{key}: {value}" for key, value in identity.items()))
            summary = load(path / "delegation-summary.json")
            summary["subagents"].append({**identity, "handoff": handoff})
            write_json(path / "delegation-summary.json", summary)
            timeline = lines(path / "timeline.jsonl")
            timeline.extend([{**identity, "stage": stage, "status": "pass", "execution_mode": "subagent", "artifacts": [handoff]} for stage in ("spawned", "handoff")])
            write_jsonl(path / "timeline.jsonl", timeline)
            write_jsonl(path / "agents/reviewer/trace.jsonl", timeline)
        check("two native sessions preserve reviewer assignment across root successor", native_successor)
        def native_followup(path, data, *, target="reviewer-plan", elevated=False, pending=False):
            native(path, data)
            assignment = data["assignments"][1]
            if elevated or pending:
                escalate(path, assignment)
            if elevated:
                invoke(path, assignment)
            invoke(path, assignment, second=25, effort="xhigh" if elevated or pending else "high")
            invocation = assignment["events"][-1]
            parent_source = "model-evidence/root-rollout.jsonl"
            records = lines(path / parent_source)
            first = len(records) + 1
            records.extend([
                {"timestamp": stamp(24), "type": "response_item", "payload": {"type": "function_call", "namespace": "collaboration", "name": "followup_task", "call_id": "call-followup", "arguments": json.dumps({"target": target})}},
                {"timestamp": stamp(24), "type": "event_msg", "payload": {"type": "item_completed", "thread_id": ROOT_SESSION, "item": {"type": "SubAgentActivity", "kind": "interacted", "id": "call-followup", "agent_thread_id": assignment["sessions"][0]["session_id"], "agent_path": "/root/reviewer-plan"}}},
                {"timestamp": stamp(24), "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-followup", "output": ""}},
            ])
            write_jsonl(path / parent_source, records)
            child_source = assignment["sessions"][0]["session_evidence"]["path"]
            child = lines(path / child_source)
            child.append({"timestamp": stamp(25), "type": "event_msg", "payload": {"type": "task_started", "turn_id": invocation["turn_id"]}})
            write_jsonl(path / child_source, child)
            invocation["request_evidence"] = {"kind": "native-followup", "call": {"path": parent_source, "line": first}, "activity": {"path": parent_source, "line": first + 1}, "result": {"path": parent_source, "line": first + 2}, "started": {"path": child_source, "line": len(child)}}

        check("native relative followup preserves baseline", native_followup)
        check("native canonical followup preserves elevated effort", lambda p, d: native_followup(p, d, target="/root/reviewer-plan", elevated=True))
        check("native followup wrong target", lambda p, d: native_followup(p, d, target="reviewer-final"), "target differs from child")
        check("native followup cannot perform escalation", lambda p, d: native_followup(p, d, pending=True), "cannot apply a pending escalation")
        def change_followup(path, data, part, change):
            native_followup(path, data)
            pointer = data["assignments"][1]["events"][-1]["request_evidence"][part]
            records = lines(path / pointer["path"])
            change(records[pointer["line"] - 1])
            write_jsonl(path / pointer["path"], records)
        check("native followup wrong child activity", lambda p, d: change_followup(p, d, "activity", lambda r: r["payload"]["item"].update(agent_thread_id=ROOT_SESSION)), "matching child interaction")
        check("native followup wrong result call", lambda p, d: change_followup(p, d, "result", lambda r: r["payload"].update(call_id="other-call")), "matching tool result")
        check("native followup forbids settings arguments", lambda p, d: change_followup(p, d, "call", lambda r: r["payload"].update(arguments=json.dumps({"target": "reviewer-plan", "effort": "xhigh"}))), "cannot request model settings changes")
        check("native followup cannot predate interaction", lambda p, d: change_followup(p, d, "started", lambda r: r.update(timestamp=stamp(23))), "started before its source interaction")
        def followup_drift(path, data):
            native_followup(path, data, elevated=True)
            invocation = data["assignments"][1]["events"][-1]
            invocation["effort"] = "high"
            records = lines(path / invocation["evidence"]["path"])
            records[invocation["evidence"]["line"] - 1]["payload"]["effort"] = "high"
            write_jsonl(path / invocation["evidence"]["path"], records)
        check("native followup observed downshift rejected", followup_drift, "downshift forbidden")
        check("handoff ID suffix rejected", lambda p, d: (p / "handoff-reviewer-final.md").write_text("assignment_id: reviewer-final\ncodex_thread_id: fixture-session-reviewer-finalevil\nparent_thread_id: fixture-root"), "missing/mismatched codex_thread_id")

        def prelaunch(path, data):
            assignment = data["assignments"][2]
            invocation = assignment["events"].pop()
            escalate(path, assignment, second=9)
            decision = lines(path / "decision.jsonl")
            decision[0]["params"].update(session_id=None, parent_session_id=ROOT_SESSION)
            write_jsonl(path / "decision.jsonl", decision)
            assignment["events"].append(invocation)
            invocation["effort"] = "xhigh"
            source = path / "model-evidence/reviewer-final-tool.jsonl"
            records = lines(source)
            records[0]["result"]["reasoningEffort"] = "xhigh"
            records[1]["params"]["effort"] = "xhigh"
            write_jsonl(source, records)
            source = path / "model-evidence/reviewer-final-rollout.jsonl"
            records = lines(source)
            records[1]["payload"]["effort"] = "xhigh"
            write_jsonl(source, records)
        check("qa-critical prelaunch has no invented future ID", prelaunch)
        def recovery(path, data):
            assignment = data["assignments"][0]
            write_json(path / "risk-resolutions.json", {"resolutions": [{"risk_id": "fixture-risk", "attempts": [{"attempt": 1, "status": "blocked"}, {"attempt": 2, "owner_lane": "root"}], "blocked_recovery": {"senior_qa_test_design_review": {"lane": "senior"}, "architect_review": {"lane": "architect"}}}]})
            assignment["events"].append({"kind": "recovery", "session_id": ROOT_SESSION, "at": stamp(20), "attempt": 1, "next_attempt": 2, "risk_id": "fixture-risk"})
            invoke(path, assignment, effort="high")
            assignment["events"][-1]["attempt"] = 2
        check("recovery preserves existing attempt chain", recovery)
        check("recovery cannot exceed limit", lambda p, d: (recovery(p, d), d["assignments"][0]["events"][1].update(next_attempt=4)), "three-attempt limit")
        check("recovery requires original record", lambda p, d: (recovery(p, d), d["assignments"][0]["events"][1].update(risk_id="fake")), "existing risk resolution")

        untrusted = root / "untrusted"
        make_run(untrusted)
        assert any("original rollout inside CODEX_HOME/sessions" in error for error in validate_model_settings(untrusted))
        count += 1
        trusted = stage_test_observations(untrusted)
        with patch.dict(os.environ, {"CODEX_HOME": str(trusted)}):
            assert not validate_model_settings(untrusted)
            data = load(untrusted / "model-settings.json")
            data["assignments"][1]["sessions"][0]["launch_evidence"] = {"path": "model-evidence/reviewer-plan-tool.jsonl", "line": 1}
            write_json(untrusted / "model-settings.json", data)
            assert any("child requires source-bound native launch" in error for error in validate_model_settings(untrusted))
            count += 1
            source = Path(data["assignments"][0]["sessions"][0]["session_evidence"]["path"])
            source.unlink()
            source.symlink_to(untrusted / "model-evidence/root-rollout.jsonl")
            assert any("original rollout inside CODEX_HOME/sessions" in error for error in validate_model_settings(untrusted))
            count += 1
        pending = root / "pending"
        pending.mkdir()
        assert not validate_model_settings(pending, allow_pending=True)
        assert validate_model_settings(pending)
        write_jsonl(pending / "timeline.jsonl", [{"stage": "spawned"}])
        assert validate_model_settings(pending, allow_pending=True)
        write_json(pending / "model-settings.json", {})
        assert any("schema_version" in error for error in validate_model_settings(pending, allow_pending=True))
        count += 4

        # Recorder rejects invalid spawn before creating even an empty directory.
        path = root / "recorder"
        path.mkdir()
        recorder = Path(__file__).with_name("record-agent-trace.py")
        base = [sys.executable, str(recorder), "--run-dir", str(path), "--role", "reviewer", "--status", "pass", "--summary", "fixture"]
        for stage in ("spawn", "spawned"):
            result = subprocess.run(base + ["--stage", stage], capture_output=True, text=True)
            assert result.returncode != 0 and not list(path.iterdir()), (stage, result.stdout, result.stderr)
        count += 2
        valid = root / "recorder-valid"
        data = make_run(valid)
        session = data["assignments"][1]["sessions"][0]
        args = [sys.executable, str(recorder), "--run-dir", str(valid), "--role", "reviewer", "--status", "running", "--summary", "fixture", "--assignment-id", "reviewer-plan", "--parent-thread-id", ROOT_SESSION, "--codex-thread-id", session["session_id"]]
        result = subprocess.run(args + ["--stage", "working-custom-stage"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        count += 1
    print(f"PASS model settings: {count} scenarios")


def change_context_model(path):
    source = path / "model-evidence/root-rollout.jsonl"
    records = lines(source)
    records[1]["payload"]["model"] = "gpt-5.6-sol"
    write_jsonl(source, records)


def reset_snapshot_attempt(path):
    snapshot = load(path / "context-next.json")
    snapshot["attempt"] = 0
    write_json(path / "context-next.json", snapshot)


if __name__ == "__main__":
    run_model_settings_tests()
