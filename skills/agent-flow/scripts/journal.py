#!/usr/bin/env python3
"""Publish captured documents, read a pinned document, or repair a revision view."""
import argparse
import json
from pathlib import Path
import sys

from journal_io import (JournalError, JournalSnapshot, digest, export_snapshot,
                        logical_path, operation_id, transact, capture_file, capture_external_references)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("contract", help="Print the actual semantic writer contract.")
    record = commands.add_parser("record")
    record.add_argument("--kind", required=True)
    record.add_argument("--request-file", required=True)
    record.add_argument("--operation-id")
    publish = commands.add_parser("publish")
    publish.add_argument("--file", action="append", nargs=2, metavar=("LOGICAL_PATH", "CAPTURE"), required=True)
    publish.add_argument("--operation-id", help="Saved machine request ID for a technical retry.")
    read = commands.add_parser("read")
    read.add_argument("path")
    read.add_argument("--archive")
    export = commands.add_parser("export")
    export.add_argument("--archive")
    commands.add_parser("diagnose-recovery")
    reopen = commands.add_parser("reopen")
    for name in ("expected-run-uuid", "operation-id", "reason", "identity-file"):
        reopen.add_argument("--" + name, required=True)
    for name in ("expected-revision", "expected-generation"):
        reopen.add_argument("--" + name, required=True, type=int)
    reopen.add_argument("--final-index", type=int, required=True)
    reopen.add_argument("--final-sha256", required=True)
    finalize_parser = commands.add_parser("finalize", help="Validate and atomically close an open generation.")
    finalize_parser.add_argument("--expected-run-uuid", help="CAS assertion; defaults to the current run identity.")
    finalize_parser.add_argument("--expected-revision", type=int, help="CAS assertion; defaults to the current revision.")
    finalize_parser.add_argument("--expected-generation", type=int, help="CAS assertion; defaults to the current generation.")
    finalize_parser.add_argument("--operation-id", help="Saved machine request ID for a technical retry; a fresh one is generated and printed when omitted.")
    final_input = finalize_parser.add_mutually_exclusive_group(required=True)
    final_input.add_argument("--final-file")
    final_input.add_argument("--summary")
    finalize_parser.add_argument("--verdict", choices=("ship", "blocked", "fail", "pass-with-risks"))
    finalize_parser.add_argument("--status", choices=("pass", "blocked", "fail", "pass-with-risks"))
    resolution_parser = commands.add_parser("resolve-obligation", help="Record evidence that another assignment covers the same responsibility.")
    resolution_parser.add_argument("--lane-id", required=True)
    resolution_parser.add_argument("--replacement", required=True)
    resolution_parser.add_argument("--reason", required=True)
    resolution_parser.add_argument("--expected-revision", type=int, required=True)
    resolution_parser.add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "contract":
        from journal_structure import request_contract
        print(json.dumps(request_contract(), ensure_ascii=False, indent=2))
        return 0
    if not args.run_dir:
        parser.error("--run-dir is required for journal operations")
    run_dir = Path(args.run_dir).expanduser().absolute()
    try:
        if args.command == "record":
            from journal_io import write_entry
            request = json.loads(capture_file(Path(args.request_file)))
            print(json.dumps(write_entry(run_dir, args.kind, request, identifier=args.operation_id)))
        elif args.command == "diagnose-recovery":
            from journal_recovery import diagnose_recovery
            print(json.dumps(diagnose_recovery(run_dir), ensure_ascii=False, indent=2))
        elif args.command == "reopen":
            from journal_recovery import recover
            receipt = recover(run_dir, expected_run_uuid=args.expected_run_uuid,
                expected_revision=args.expected_revision, expected_generation=args.expected_generation,
                identifier=args.operation_id, reason=args.reason,
                identity=json.loads(capture_file(Path(args.identity_file))),
                final_index=args.final_index, final_sha256=args.final_sha256)
            print(json.dumps({"operation_id": args.operation_id, **receipt}))
        elif args.command == "publish":
            current = JournalSnapshot.open(run_dir)
            def check_owned(snapshot, names):
                lane_map = json.loads(snapshot.documents.get("lane-map.json", b"{}"))
                summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
                lane_ids = {lane["id"] for lane in lane_map.get("lanes", [])}
                lane_ids.update(record["lane_id"] for group in ("subagents", "role_lanes") for record in summary.get(group, []))
                from journal_structure import writer_owned_path
                for name in names:
                    if writer_owned_path(name, lane_ids=lane_ids):
                        raise JournalError("writer-owned document requires semantic recorder: " + name)
            check_owned(current, [name for name, capture in args.file])
            documents = {}
            for name, capture in args.file:
                logical_path(name)
                from journal_structure import writer_owned_path
                if writer_owned_path(name):
                    raise JournalError("writer-owned document requires semantic recorder: " + name)
                path = Path(capture)
                if path.is_symlink():
                    raise JournalError(f"capture must not be a symlink: {path}")
                if name in documents:
                    raise JournalError(f"duplicate document: {name}")
                documents[name] = capture_file(path)
            documents = capture_external_references(run_dir, documents, current.source_root, previous=current.documents)
            payload = {name: digest(data) if data is not None else None for name, data in documents.items()}
            identifier = operation_id(run_dir, "publish", payload, identifier=args.operation_id)
            def publish_documents(snapshot):
                check_owned(snapshot, [name for name, capture in args.file])
                published = dict(documents)
                index_path = "artifacts/source-references.json"
                if index_path in published:
                    index = json.loads(snapshot.documents.get(index_path, b"{}"))
                    index.update(json.loads(published[index_path]))
                    published[index_path] = (json.dumps(index, indent=2, sort_keys=True) + "\n").encode()
                return published, {}
            receipt = transact(run_dir, identifier, payload, publish_documents)
            print(json.dumps({"operation_id": identifier, **receipt}))
        elif args.command == "resolve-obligation":
            from journal_lifecycle import resolve_obligation
            receipt = resolve_obligation(run_dir, lane_id=args.lane_id, replacement=args.replacement,
                                         reason=args.reason, expected_revision=args.expected_revision,
                                         identifier=args.operation_id)
            print(json.dumps({"operation_id": args.operation_id, **receipt}))
        elif args.command == "finalize":
            import uuid
            from journal_lifecycle import finalize
            snapshot = JournalSnapshot.open(run_dir)
            identifier = args.operation_id or str(uuid.uuid4())
            prior = snapshot.operation_receipt(identifier)
            if args.final_file and prior is None:
                raise JournalError("new finalization requires --summary; --final-file is available only for exact historical receipt replay")
            verdict = "ship" if args.status == "pass" else args.status or args.verdict
            if not verdict or args.verdict and args.verdict != verdict:
                raise JournalError("finalize requires matching status/verdict")
            receipt = finalize(run_dir, expected_run_uuid=args.expected_run_uuid or snapshot.run_uuid,
                               expected_revision=args.expected_revision if args.expected_revision is not None
                               else prior["result"].get("validated_revision", snapshot.revision) if prior else snapshot.revision,
                               expected_generation=args.expected_generation if args.expected_generation is not None
                               else prior["result"].get("generation", snapshot.generation) if prior else snapshot.generation,
                               identifier=identifier, final_bytes=capture_file(Path(args.final_file)) if args.final_file else None,
                               final_summary=args.summary, verdict=verdict)
            print(json.dumps({"operation_id": identifier, **receipt}))
        elif args.command == "read":
            snapshot = JournalSnapshot.open(run_dir)
            sys.stdout.buffer.write((snapshot.archive(args.archive) if args.archive else snapshot).read_bytes(args.path))
        else:
            snapshot = JournalSnapshot.open(run_dir)
            print(export_snapshot(snapshot.archive(args.archive) if args.archive else snapshot))
    except (JournalError, OSError, ValueError) as exc:
        parser.exit(1, f"journal: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
