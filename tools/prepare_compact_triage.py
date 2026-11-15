"""Prepare private compact AI triage packets from an existing offline export."""
import sys

from career_engine.compact_triage import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_PROMPT_BYTES,
    ExportError,
    json_line,
    prepare_compact_triage,
)
from tools.export_immediate_review import Parser


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--export-dir", required=True, help="Existing records.jsonl and manifest.json")
    parser.add_argument("--targeted-snapshot", required=True, help="Saved targeted snapshot used in the export")
    parser.add_argument("--output-dir", required=True, help="Private output directory outside Git")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-prompt-bytes", type=int, default=DEFAULT_MAX_PROMPT_BYTES)
    args = parser.parse_args(argv)
    try:
        bundle, manifest = prepare_compact_triage(
            args.export_dir, args.targeted_snapshot, args.output_dir, args.batch_size, args.max_prompt_bytes,
        )
    except ExportError as error:
        print(error, file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, StopIteration, RecursionError):
        print("Preparation failed. Check the exporter bundle, saved snapshot and private output permissions.",
              file=sys.stderr)
        return 2

    targeted = [packet for packet in manifest["packets"] if packet["source_tier"] == "TARGETED"]
    print(json_line({
        "bundle": bundle.name, "counts_by_tier": manifest["counts_by_tier"],
        "packet_count": len(manifest["packets"]), "calibration_packets": targeted[:2],
    }).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
