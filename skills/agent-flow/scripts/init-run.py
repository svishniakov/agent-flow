#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace

from journal_io import JournalError, JournalSnapshot, initialize_journal, require
from journal_structure import encoded, record_id, replay
from task_workspace import bind_workspace
from verification_evidence import CodexSessionSource, own_metadata, requirements


def initialize(run_dir, *, repo, summary, root_agent_id, requirement_source, roots=None,
               context=None, memory=None, identifier, source=None):
    repo = Path(repo).expanduser().resolve()
    run_dir = Path(run_dir).expanduser().absolute()
    require(isinstance(summary, str) and bool(summary.strip()), "summary is required")
    requirement_source = dict(requirement_source)
    requirement_source["path"] = str((repo / requirement_source["path"]).absolute())
    task = {"summary": summary, "requirements": requirement_source}
    declared_memory = sorted(set([requirement_source["path"], *(memory or [])]))
    payload = {"kind": "initialize", "request": {"source_root": str(repo), "task": task,
               "root_agent_id": root_agent_id, "roots": roots or [], "context": context or [],
               "memory": declared_memory}}
    if (run_dir / ".journal/state.sqlite3").exists():
        snapshot = JournalSnapshot.open(run_dir)
        prior = replay(snapshot, identifier, payload)
        require(prior is not None, "task already exists; choose a new slug")
        return snapshot
    source = source or CodexSessionSource()
    own_metadata(source.read(root_agent_id), root_agent_id)
    probe = SimpleNamespace(source_root=repo, json=lambda name: task)
    requirements(probe)
    scope = bind_workspace(repo, roots=roots, context=context, memory=declared_memory, run_dir=run_dir)
    return initialize_journal(run_dir, {"task.json": encoded(task), "scope.json": encoded(scope)},
                              source_root=repo, identities={"root_agent_id": root_agent_id}, identifier=identifier,
                              payload=payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize a task bound to real source roots and a single acceptance source.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--root-agent-id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--task-heading", required=True)
    parser.add_argument("--criteria-heading", default="Критерии приёмки")
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--memory", action="append", default=[])
    parser.add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    try:
        slug = record_id(args.slug)
        repo = Path(args.repo).expanduser().resolve()
        run_dir = repo / ".agent-work" / "runs" / (datetime.now().astimezone().strftime("%Y-%m-%d") + "-" + slug)
        snapshot = initialize(run_dir, repo=repo, summary=args.summary, root_agent_id=args.root_agent_id,
            requirement_source={"path": args.requirements, "task_heading": args.task_heading,
                                "criteria_heading": args.criteria_heading},
            roots=[{"path": str(Path(path).expanduser().absolute())} for path in args.root],
            context=[str(Path(path).expanduser().absolute()) for path in args.context],
            memory=[str(Path(path).expanduser().absolute()) for path in args.memory],
            identifier=args.operation_id)
        print(json.dumps({"run_dir": str(snapshot.logical_root), "run_uuid": snapshot.run_uuid,
                          "revision": snapshot.revision}))
    except (JournalError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"init-run: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
