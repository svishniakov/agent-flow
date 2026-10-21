#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

from journal_io import JournalError, JournalSnapshot, capture_file
from journal_structure import markdown_view, publish_artifact, read_record, request_contract, write_entry


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record verified task data and read its current result.")
    parser.add_argument("--run-dir")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("contract")
    record = commands.add_parser("record")
    record.add_argument("--kind", required=True, choices=("assignment", "scope", "report", "check", "generation"))
    record.add_argument("--request-file", required=True, help="JSON file or - for stdin.")
    record.add_argument("--operation-id", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--file", nargs=2, metavar=("LOGICAL_PATH", "SOURCE"), required=True)
    publish.add_argument("--operation-id", required=True)
    read = commands.add_parser("read")
    read.add_argument("path")
    read.add_argument("--format", choices=("json", "markdown"), default="json")
    final = commands.add_parser("finalize")
    final.add_argument("--status", choices=("completed", "failed"), required=True)
    final.add_argument("--summary", required=True)
    final.add_argument("--reason")
    final.add_argument("--unverified", action="append", default=[])
    final.add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            result = request_contract()
        else:
            require_run = args.run_dir
            if not require_run:
                parser.error("--run-dir is required")
            run_dir = Path(require_run).expanduser().absolute()
            if args.command == "record":
                raw = sys.stdin.buffer.read() if args.request_file == "-" else capture_file(Path(args.request_file))
                result = write_entry(run_dir, args.kind, json.loads(raw), identifier=args.operation_id)
            elif args.command == "publish":
                result = publish_artifact(run_dir, *args.file, identifier=args.operation_id)
            elif args.command == "read":
                snapshot = JournalSnapshot.open(run_dir)
                if args.path.startswith("artifacts/"):
                    sys.stdout.buffer.write(snapshot.read_bytes(args.path))
                    return 0
                result = read_record(snapshot, args.path)
                if args.format == "markdown":
                    print(markdown_view(result), end="")
                    return 0
            else:
                from journal_lifecycle import finalize
                result = finalize(run_dir, identifier=args.operation_id, status=args.status,
                                  summary=args.summary, reason=args.reason, unverified=args.unverified)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "record" and args.kind == "generation" and result["receipt"]["result"]["exit_code"] != 0:
            return 1
    except (JournalError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"journal: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
