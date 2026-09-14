#!/usr/bin/env python3
"""Prepare an isolated full workspace and retain the sealed task candidate."""
import argparse
import json
from pathlib import Path

from task_workspace import WorkspaceError, prepare, seal, inspect, delivery


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    initial = commands.add_parser("prepare")
    initial.add_argument("--source", required=True)
    initial.add_argument("--destination", required=True)
    initial.add_argument("--metadata-namespace", choices=["agent-flow-runtime"])
    initial.add_argument("--metadata-authorized-source")
    for name in ("scope", "qa-proof", "review-proof"):
        initial.add_argument("--metadata-" + name + "-ref", nargs=2, metavar=("PATH", "SHA256"))
    commands.add_parser("inspect")
    sealing = commands.add_parser("seal")
    sealing.add_argument("--operation-id")
    delivering = commands.add_parser("delivery")
    delivering.add_argument("--operation-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            fields = (args.metadata_namespace, args.metadata_authorized_source, args.metadata_scope_ref,
                      args.metadata_qa_proof_ref, args.metadata_review_proof_ref)
            if any(fields) and not all(fields):
                parser.error("all five metadata scope arguments are required together")
            scope = None
            if all(fields):
                scope = {"namespace": args.metadata_namespace, "authorized_source": args.metadata_authorized_source}
                for key, value in (("scope_ref", args.metadata_scope_ref), ("qa_proof_ref", args.metadata_qa_proof_ref),
                                   ("reviewer_proof_ref", args.metadata_review_proof_ref)):
                    scope[key] = {"path": value[0], "sha256": value[1]}
            result = prepare(Path(args.run_dir), Path(args.source), Path(args.destination), metadata_scope=scope)
        elif args.command == "seal":
            result = seal(Path(args.run_dir), identifier=args.operation_id)
        elif args.command == "delivery":
            result = delivery(Path(args.run_dir), identifier=args.operation_id)
        else:
            result = inspect(Path(args.run_dir))
    except (WorkspaceError, OSError, ValueError) as exc:
        parser.exit(1, f"workspace: {exc}\n")
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
