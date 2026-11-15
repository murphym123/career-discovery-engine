"""Validate a raw compact triage JSON response and checkpoint accepted results."""
import sqlite3
import sys

from career_engine.compact_triage import ExportError, accept_compact_triage, json_line
from tools.export_immediate_review import Parser


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--packet", required=True, help="Expected packet JSON from a private prepared bundle")
    parser.add_argument("--response", required=True, help="Raw model response JSON file")
    args = parser.parse_args(argv)
    try:
        report = accept_compact_triage(args.packet, args.response)
    except ExportError as error:
        print(error, file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error):
        print("Acceptance failed. Check the JSON inputs and private checkpoint permissions.", file=sys.stderr)
        return 2
    print(json_line(report).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
