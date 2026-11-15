"""Import pinned public ATS catalogs, validate candidates, and publish verified boards."""
import argparse
import json
import os
from pathlib import Path

from career_engine.board_catalogs import CATALOG_CAMPAIGN, import_catalogs
from career_engine.board_discovery_state import DiscoveryState
from career_engine.store import Store
from career_engine.store.blobs import managed_path, outside_git
from tools.discover_boards import add_validation_options, run_discovery


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Private local Prospect data directory outside Git")
    commands = parser.add_subparsers(dest="command", required=True)
    intake = commands.add_parser("import", help="Import artifact evidence without calling ATS boards")
    intake.add_argument("--manifest", type=Path, required=True, help="Private JSON manifest with source pins")
    intake.add_argument("--offline", action="store_true", help="Require local files for every object")
    intake.add_argument("--contact", default="", help="Public HTTP User-Agent contact for remote artifacts")
    intake.add_argument("--timeout", type=float, default=30, help="Seconds per catalog HTTP request")
    status = commands.add_parser("status", help="Show source counts and validation outcomes offline")
    status.add_argument("--unsupported", action="store_true", help="Include Recruitee and Rippling observations")
    validate = commands.add_parser("validate", help="Run or resume catalog board probes with the discovery scheduler")
    add_validation_options(validate)
    publish = commands.add_parser("publish", help="Merge verified catalog boards into the existing registry offline")
    publish.add_argument("--registry", type=Path, help="Defaults to ats-registry.json in the data directory")
    args = parser.parse_args(argv)

    mask = os.umask(0o077)
    try:
        data_dir = outside_git(args.data_dir)
        with Store.in_data_dir(data_dir, create=args.command == "import") as store:
            state = DiscoveryState(store)
            if args.command == "import":
                result = import_catalogs(state, args.manifest, offline=args.offline,
                                         contact=args.contact, timeout=args.timeout)
            elif args.command == "status":
                result = state.summary(CATALOG_CAMPAIGN)
                if args.unsupported:
                    result["unsupported_observations"] = state.unsupported_catalogs()
            elif args.command == "validate":
                result = run_discovery(state, args, CATALOG_CAMPAIGN, None, mode="validate", pages=1)
            else:
                path = outside_git(args.registry) if args.registry else managed_path(data_dir, "ats-registry.json")
                result = state.publish(path, catalog_only=True)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    finally:
        os.umask(mask)
    print(json.dumps(result, indent=2))
    return 130 if result.get("stop_reason") == "interrupted" else 0


if __name__ == "__main__":
    raise SystemExit(main())
