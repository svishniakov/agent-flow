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
    commands.add_parser("export")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().absolute()
    try:
        if args.command == "publish":
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
        elif args.command == "read":
            sys.stdout.buffer.write(JournalSnapshot.open(run_dir).read_bytes(args.path))
        else:
            print(export_snapshot(JournalSnapshot.open(run_dir)))
    except (JournalError, OSError, ValueError) as exc:
        parser.exit(1, f"journal: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
