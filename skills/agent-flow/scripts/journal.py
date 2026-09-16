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
    parser.add_argument("--run-dir", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--file", action="append", nargs=2, metavar=("LOGICAL_PATH", "CAPTURE"), required=True)
    publish.add_argument("--operation-id", help="Saved machine request ID for a technical retry.")
    read = commands.add_parser("read")
    read.add_argument("path")
    read.add_argument("--archive")
    export = commands.add_parser("export")
    export.add_argument("--archive")
    commands.add_parser("diagnose-recovery")
    legacy = commands.add_parser("import-legacy")
    legacy.add_argument("--source-root", required=True)
    for command in ("upgrade", "reopen"):
        recovery = commands.add_parser(command)
        for name in ("expected-run-uuid", "operation-id", "reason", "identity-file"):
            recovery.add_argument("--" + name, required=True)
        for name in ("expected-revision", "expected-generation"):
            recovery.add_argument("--" + name, required=True, type=int)
        if command == "reopen":
            recovery.add_argument("--upgrade", action="store_true")
            recovery.add_argument("--final-index", type=int, required=True)
            recovery.add_argument("--final-sha256", required=True)
            recovery.add_argument("--failed-validation-path")
    classification = commands.add_parser("classify-legacy")
    classification.add_argument("--classification-file", required=True)
    classification.add_argument("--expected-revision", type=int, required=True)
    classification.add_argument("--operation-id", required=True)
    finalize_parser = commands.add_parser("finalize", help="Validate and atomically close an open generation.")
    finalize_parser.add_argument("--expected-run-uuid", required=True)
    finalize_parser.add_argument("--expected-revision", type=int, required=True)
    finalize_parser.add_argument("--expected-generation", type=int, required=True)
    finalize_parser.add_argument("--operation-id", required=True)
    finalize_parser.add_argument("--final-file", required=True)
    finalize_parser.add_argument("--verdict", required=True, choices=("ship", "blocked", "fail", "pass-with-risks"))
    resolution_parser = commands.add_parser("resolve-obligation", help="Record evidence that another assignment covers the same responsibility.")
    resolution_parser.add_argument("--lane-id", required=True)
    resolution_parser.add_argument("--replacement", required=True)
    resolution_parser.add_argument("--reason", required=True)
    resolution_parser.add_argument("--expected-revision", type=int, required=True)
    resolution_parser.add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().absolute()
    try:
        if args.command == "diagnose-recovery":
            from journal_recovery import diagnose_recovery
            print(json.dumps(diagnose_recovery(run_dir), ensure_ascii=False, indent=2))
        elif args.command == "import-legacy":
            from journal_io import import_legacy
            snapshot = import_legacy(run_dir, source_root=Path(args.source_root))
            print(json.dumps({"revision": snapshot.revision, "storage_version": snapshot.storage_version}))
        elif args.command in {"upgrade", "reopen"}:
            from journal_recovery import recover
            receipt = recover(run_dir, upgrade=args.command == "upgrade" or args.upgrade,
                reopen=args.command == "reopen", expected_run_uuid=args.expected_run_uuid,
                expected_revision=args.expected_revision, expected_generation=args.expected_generation,
                identifier=args.operation_id, reason=args.reason,
                identity=json.loads(capture_file(Path(args.identity_file))),
                final_index=getattr(args, "final_index", None), final_sha256=getattr(args, "final_sha256", None),
                failed_validation_path=getattr(args, "failed_validation_path", None))
            print(json.dumps({"operation_id": args.operation_id, **receipt}))
        elif args.command == "classify-legacy":
            from journal_recovery import classify_legacy
            print(json.dumps({"operation_id": args.operation_id, **classify_legacy(run_dir, json.loads(capture_file(Path(args.classification_file))),
                expected_revision=args.expected_revision, identifier=args.operation_id)}))
        elif args.command == "publish":
            documents = {}
            for name, capture in args.file:
                logical_path(name)
                path = Path(capture)
                if path.is_symlink():
                    raise JournalError(f"capture must not be a symlink: {path}")
                if name in documents:
                    raise JournalError(f"duplicate document: {name}")
                documents[name] = capture_file(path)
            current = JournalSnapshot.open(run_dir)
            documents = capture_external_references(run_dir, documents, current.source_root, previous=current.documents)
            payload = {name: digest(data) if data is not None else None for name, data in documents.items()}
            identifier = operation_id(run_dir, "publish", payload, identifier=args.operation_id)
            def publish_documents(snapshot):
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
            from journal_lifecycle import finalize
            receipt = finalize(run_dir, expected_run_uuid=args.expected_run_uuid,
                               expected_revision=args.expected_revision, expected_generation=args.expected_generation,
                               identifier=args.operation_id, final_bytes=capture_file(Path(args.final_file)),
                               verdict=args.verdict)
            print(json.dumps({"operation_id": args.operation_id, **receipt}))
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
