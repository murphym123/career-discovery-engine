"""Export saved public-board JSON snapshots for private offline review."""
import argparse
import sys

from career_engine.immediate_review import DEFAULT_BATCH_SIZE, ExportError, export_snapshots, json_line


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        # Argument values can include private paths or pasted source URLs.
        self.exit(2, "Invalid arguments. Use --help for usage.\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, help="Saved snapshot (repeatable)")
    parser.add_argument("--output-dir", required=True, help="Private directory outside Git")
    parser.add_argument("--max-records", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Maximum review records per batch (default {DEFAULT_BATCH_SIZE})")
    args = parser.parse_args(argv)
    try:
        target, manifest = export_snapshots(args.input, args.output_dir, args.max_records)
    except ExportError as error:
        print(error, file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("Export failed. Check the private output directory and its permissions.", file=sys.stderr)
        return 2

    print(json_line({
        "bundle": target.name, "manifest_sha256": target.name.removeprefix("review-"),
        "corpus_sha256": manifest["corpus"]["sha256"], "counts": manifest["counts"],
        "batches": len(manifest["batches"]), "omitted_urls": sum(manifest["omitted_urls_by_field"].values()),
    }).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
