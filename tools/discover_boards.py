"""Opt-in ATS board discovery and publication CLI."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import signal

from career_engine.board_discovery import Discovery, Limits
from career_engine.board_discovery_http import RequestsTransport
from career_engine.board_discovery_state import DiscoveryState
from career_engine.board_indexes import Plan
from career_engine.store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="Existing or new local Prospect data directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Explicitly opt in to public index and/or board probes")
    run.add_argument("--campaign", required=True)
    run.add_argument("--through", required=True, help="Fixed UTC date/time with offset, e.g. 2026-11-14T00:00:00Z")
    run.add_argument("--archive-from", default="", help="Optional Wayback start date (YYYYMMDD)")
    run.add_argument("--recent-days", type=int, default=30, help="urlscan lookback, or 0 to disable")
    run.add_argument("--page-size", type=int, default=2_000, help="Wayback rows per request")
    run.add_argument("--urlscan-size", type=int, default=100)
    run.add_argument("--commoncrawl", default="", metavar="COLLECTION", help="Enable eligible fallback to a CC-MAIN collection")
    run.add_argument("--mode", choices=("both", "index", "validate"), default="both")
    run.add_argument("--contact", required=True, help="Public operator contact for the HTTP User-Agent")
    run.add_argument("--max-requests", type=int, required=True)
    run.add_argument("--max-pages", type=int, required=True, help="Index HTTP attempts, including count calls")
    run.add_argument("--concurrency", type=int, default=8)
    run.add_argument("--max-bytes", type=int, default=256_000_000)
    run.add_argument("--response-bytes", type=int, default=4_000_000)
    run.add_argument("--max-seconds", type=float, default=3600)
    run.add_argument("--timeout", type=float, default=30)
    run.add_argument("--idle-seconds", type=float, default=5)
    run.add_argument("--retry-delay", type=float, default=60, help="Minimum cooldown for 429 or 503")
    run.add_argument("--origin-delay", action="append", default=[], metavar="ORIGIN=SECONDS")
    run.add_argument("--origin-concurrency", action="append", default=[], metavar="ORIGIN=WORKERS")
    run.add_argument("--deny-prefix", action="append", default=[], metavar="HTTPS_URL_PREFIX")
    status = subparsers.add_parser("status", help="Show checkpoint and outcomes without network access")
    status.add_argument("--campaign", required=True)
    publish = subparsers.add_parser("publish", help="Merge verified boards into the v1 registry without network access")
    publish.add_argument("--registry", type=Path, required=True)
    args = parser.parse_args(argv)

    with Store.in_data_dir(Path(args.data_dir), create=args.command == "run") as store:
        state = DiscoveryState(store)
        if args.command == "publish":
            result = state.publish(args.registry)
        elif args.command == "status":
            try:
                result = state.summary(args.campaign)
            except ValueError as exc:
                parser.exit(2, f"{exc}\n")
        else:
            try:
                plan = Plan(datetime.fromisoformat(args.through.replace("Z", "+00:00")), args.archive_from,
                            args.recent_days, args.page_size, args.urlscan_size, args.commoncrawl)
                delays = {host: float(seconds) for host, seconds in (item.split("=", 1) for item in args.origin_delay)}
                slots = {host: int(count) for host, count in (item.split("=", 1) for item in args.origin_concurrency)}
                limits = Limits(args.max_requests, args.max_pages, concurrency=args.concurrency, bytes=args.max_bytes,
                                response_bytes=args.response_bytes, seconds=args.max_seconds, timeout=args.timeout,
                                idle_seconds=args.idle_seconds, retry_delay=args.retry_delay,
                                origin_delays=delays, origin_concurrency=slots)
                stopped = False

                def stop(signum, frame):
                    nonlocal stopped
                    stopped = True

                previous_term = signal.signal(signal.SIGTERM, stop)
                try:
                    with RequestsTransport(args.contact) as transport:
                        result = Discovery(state, transport, args.campaign, plan, limits, mode=args.mode,
                                           denied_prefixes=args.deny_prefix, stopping=lambda: stopped).run()
                finally:
                    signal.signal(signal.SIGTERM, previous_term)
            except (ValueError, RuntimeError) as exc:
                parser.exit(2, f"{exc}\n")
    print(json.dumps(result, indent=2))
    return 130 if result.get("stop_reason") == "interrupted" else 0


if __name__ == "__main__":
    raise SystemExit(main())
