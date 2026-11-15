# Scalable public ATS board discovery

Issue [#60](https://github.com/murphym123/career-discovery-engine/issues/60) adds `python -m tools.discover_boards`, an opt-in path for discovering and verifying public boards. It uses Wayback's CDX URL index for both Greenhouse hosts, both Lever regions and Ashby. The recent urlscan query complements Wayback with public scans. An explicitly selected Common Crawl collection can supply fallback URL evidence for a host whose Wayback stream fails, is unavailable or finishes without eligible leads. Issue [#44](https://github.com/murphym123/career-discovery-engine/issues/44) recorded a robots denial on Common Crawl's index route. The fallback checks the current route before making even a page-count request.

The domain-wide discovery, urlscan supplementation, slug filter and HEAD probe strategy adapt [Matt Herzog's `job-boards` source at `da7885c`](https://github.com/mherzog4/job-boards/blob/da7885cff552c513319318f2f31ed23f049f426e/job_boards.py). The [MIT notice](../LICENSES/job-boards-MIT.txt) accompanies the adaptation. [Public ATS catalog import](prospect-board-catalogs.md) uses its own CLI with the shared validation queue and registry.

## Run and resume

Run the fixture suite offline from the repository root:

```sh
python -m unittest discover -s tests -p test_board_discovery.py -v
python -m unittest discover -s tests -v
```

The commands below make public network requests. Use them within the authorized scope for the live run. Use an operator-controlled public contact and the exact source restrictions applicable to that run. `--deny-prefix` is repeatable and applies to both HEAD and GET. Keep the same restriction flags on resume. `--commoncrawl CC-MAIN-YYYY-NN` explicitly enables one collection and is omitted in this example.

```sh
PROSPECT_DATA_DIR="$HOME/Library/Application Support/Prospect"
PROSPECT_DISCOVERY_CONTACT='operator@example.org'   # Replace with the public operator contact.

python -m tools.discover_boards --data-dir "$PROSPECT_DATA_DIR" run \
  --campaign ats-20261114 --through 2026-11-14T00:00:00Z \
  --contact "$PROSPECT_DISCOVERY_CONTACT" \
  --max-requests 50000 --max-pages 6000 --concurrency 8 \
  --max-bytes 8000000000 --response-bytes 8000000 \
  --max-seconds 28800 --timeout 60
```

Repeat the command to resume the campaign. The campaign pins the cutoff, optional `--archive-from YYYYMMDD`, `--recent-days`, `--page-size`, `--urlscan-size` and optional Common Crawl collection. Change budgets, concurrency or timeout on resume. Use a new campaign name and cutoff to refresh source coverage. `--recent-days 0` disables urlscan. `--mode index` gathers leads only. `--mode validate` drains the shared validation queue without querying indexes, using the same campaign plan. `--mode both` can validate as pages arrive.

Robots checks and validation probes count toward `--max-requests`. The page budget counts attempted index calls, including Common Crawl page counts and failed requests. `--response-bytes` bounds a single body and `--max-bytes` bounds application body reads across the invocation. A worker reserves its response allowance before dispatch so simultaneous bodies fit the remaining budget. Leave room for at least one full allowance for continued GET work. The scheduler stops dispatching at the time budget and drains outstanding calls with their socket timeouts. Interrupt with Ctrl-C or SIGTERM and repeat the command. An interrupted request can replay, while a committed page resumes from its saved cursor.

## Pacing and outcomes

- A rotating queue serves index streams and provider origins. Independent origins can overlap, with one in-flight request per origin by default. `--origin-concurrency HOST=WORKERS` raises an origin's concurrency where allowed, within the global `--concurrency` limit and the origin's request-start interval. Each cursor and board identity has at most one request in flight. Greenhouse aliases share one API origin. Lever US and EU remain distinct identities and use the respective regional APIs.
- Default request start gaps are one second for Wayback, Lever US/EU and Common Crawl, two seconds for urlscan, and 250 ms for Greenhouse and Ashby. `--origin-delay HOST=SECONDS` changes a baseline when the source's rules permit it. Applicable robots pacing can increase the gap. Robots decisions expire after 24 hours. An observed 429/503 persists its `Retry-After` cooldown, in seconds or HTTP-date form, with a configurable minimum (`--retry-delay`). A later invocation retries the affected work after the saved deadline. Access denials and detected challenges stop new requests to the affected origin across invocations and require operator review of the recorded evidence. Requests already in flight can finish. Redirects are recorded without being followed.
- CDX resume keys, urlscan `search_after` and Common Crawl page indices are checkpointed in the same transaction as the corresponding observations. A failed, truncated or looping page does not advance its cursor. Round-robin scheduling uses the last attempted stream and verification records to carry queue fairness across invocations. `queue_drained` means the invocation has no further eligible work. Inspect stream failures, deferrals and host holds before interpreting coverage. Storage grows with distinct observations, and processing holds at most a bounded batch of response bodies in memory.
- The existing `prospect.sqlite` stores per-campaign stream states, provider/tenant identities, observed spellings, source timestamps and scan identifiers, and attempts with reason, endpoint, time, HTTP status and byte count. Recognized public job path identifiers can be retained in evidence URLs. Query strings, fragments, private scan records and raw response bodies are discarded from observations. Opaque index cursors remain local in the database. Keep the data directory outside Git and access-restricted. `status` reports streams for the named campaign and cumulative validation counts for the database.
- `head_reachable` records a successful HEAD with an API JSON media type. A missing/unsuitable media type, 405 or 501 can trigger a policy-checked bounded GET. `get_board_schema` records an expected public API response, which can contain zero jobs. These statuses concern board discovery. Existing inventory polling is responsible for actual job records, completeness and closure.

## Publish to Prospect's registry

These commands are offline:

```sh
python -m tools.discover_boards --data-dir "$PROSPECT_DATA_DIR" status --campaign ats-20261114
python -m tools.discover_boards --data-dir "$PROSPECT_DATA_DIR" publish \
  --registry "$PROSPECT_DATA_DIR/ats-registry.json"
```

Publish to the same registry path passed to the existing `tools.expand_ats` workflow, when other registry writers are idle. Publication is an idempotent, atomic merge of verified boards into the v1 document. Each published board carries verification metadata and a pointer to its first source observation in the local discovery store. The store holds additional evidence. Publication preserves prior source and poll metadata, and it does not poll, schedule or close postings. The scheduler is single-process per SQLite store. Source parsing and the transport interface are independently testable for a later scheduler.

Fixture coverage includes 5,050 verified boards through publication. Live source reachability, archive cursor behavior and coverage, endpoint status/schema behavior, and sustainable throughput still need measurement. Archive and scan coverage is partial by nature. The discovery totals do not estimate the number of open jobs or market recall.
