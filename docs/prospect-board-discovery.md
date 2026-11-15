# Scalable public ATS board discovery

Issue [#60](https://github.com/murphym123/career-discovery-engine/issues/60) adds `python -m tools.discover_boards`, an opt-in path for discovering and verifying public boards. It uses Wayback's CDX URL index for both Greenhouse hosts, both Lever regions and Ashby. The recent urlscan query complements Wayback with public scans. An explicitly selected Common Crawl collection can supply fallback URL evidence for a host whose Wayback stream fails, is unavailable or finishes without eligible leads. Issue [#44](https://github.com/murphym123/career-discovery-engine/issues/44) recorded a robots denial on Common Crawl's index route. The fallback checks the current route before making even a page-count request.

The domain-wide discovery, urlscan supplementation, slug filter and HEAD probe strategy adapt [Matt Herzog's `job-boards` source at `da7885c`](https://github.com/mherzog4/job-boards/blob/da7885cff552c513319318f2f31ed23f049f426e/job_boards.py). The [MIT notice](../LICENSES/job-boards-MIT.txt) accompanies the adaptation. This implementation imports neither the upstream seed catalog nor a third-party dataset.

## Run and resume

Run the fixture suite offline from the repository root:

```sh
python -m unittest discover -s tests -p test_board_discovery.py -v
python -m unittest discover -s tests -v
```

The commands below make public network requests. Use them within the authorized scope for the live run. Use an operator-controlled public contact and the exact source restrictions applicable to that run. Repeat `--deny-prefix HTTPS_URL_PREFIX` for routes withheld from both methods and `--deny-method-prefix GET=HTTPS_URL_PREFIX` or `HEAD=HTTPS_URL_PREFIX` for method-specific restrictions. Prefixes match the HTTPS request URL, including a query prefix if supplied. Restrictions cover robots, index and probe requests, including GET confirmation after HEAD.

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

All campaigns in the database share a persisted access policy because they share a validation queue. Omitted flags reuse that policy. To change it, pass `--replace-access-policy` with the complete replacement set. A legacy store with campaigns and no saved policy requires this explicit action before requests can resume. Use `--commoncrawl CC-MAIN-YYYY-NN` to enable one Common Crawl collection explicitly.

Robots checks and validation probes count toward `--max-requests`. The page budget counts attempted index calls, including Common Crawl page counts and failed requests. `--response-bytes` bounds a single body and `--max-bytes` bounds application body reads across the invocation. Leave room for at least one full allowance for continued GET work. Each request reserves its response allowance and has a monotonic deadline no later than the campaign cutoff. Late results are discarded. DNS and blocking socket operations can outlive the logical deadline. Such a worker retains its slot and the store lock until the physical call returns or the process exits, and its full byte allowance is charged if the actual read count is unknown. The timed-out origin gets no new work in that invocation. Interrupt with Ctrl-C or SIGTERM and repeat the command. An interrupted request can replay, while a committed page resumes from its saved cursor.

## Pacing and outcomes

- A rotating queue serves index streams and provider origins. Independent origins can overlap, with one in-flight request per origin by default. `--origin-concurrency HOST=WORKERS` raises an origin's concurrency where allowed, within the global `--concurrency` limit and the origin's request-start interval. Each cursor and board identity has at most one request in flight. Greenhouse aliases share one API origin. Lever US and EU remain distinct identities and use the respective regional APIs.
- Default request start gaps are one second for Wayback, Lever US/EU and Common Crawl, two seconds for urlscan, and 250 ms for Greenhouse and Ashby. `--origin-delay HOST=SECONDS` changes a baseline when the source's rules permit it. Applicable robots pacing can increase the gap. Starts are measured at dispatch, with a saved conservative cooldown if the process exits before checkpointing the start. Robots route denials become eligible for a fresh check when the cached rules expire after 24 hours. Operator denials require explicit policy replacement. An ordinary 429/503 persists its `Retry-After` cooldown, in seconds or HTTP-date form, with a configurable minimum (`--retry-delay`). Publisher access denials and detected challenges stop new requests to the affected origin across invocations and require operator review. Challenge detection can inspect a bounded GET response prefix, including on errors. Requests already sent can finish. Redirects are recorded without being followed.
- CDX resume keys, urlscan `search_after` and Common Crawl page indices are checkpointed in the same transaction as the corresponding observations. CDX repetition uses the decoded value sent on the wire. A failed, truncated or looping page does not advance its cursor. Common Crawl captures must fall within the pinned UTC archive window. Stream status counts out-of-window rows among its rejections, and legacy ineligible observations remain inspectable in SQLite with a separate status count. Round-robin scheduling uses the last attempted stream and verification records to carry queue fairness across invocations. `queue_drained` means the invocation has no further eligible work. Inspect stream failures, deferrals and host holds before interpreting coverage. Storage grows with distinct observations, and processing holds at most a bounded batch of response bodies in memory.
- The existing `prospect.sqlite` stores per-campaign stream states, provider/tenant identities, observed spellings, source timestamps and scan identifiers, and attempts with reason, endpoint, time, HTTP status and byte count. Recognized public job path identifiers can be retained in evidence URLs. Query strings, fragments, private scan records and raw response bodies are discarded from observations. Opaque index cursors remain local in the database. Keep the data directory outside Git and access-restricted. `status` reports streams for the named campaign and cumulative validation counts for the database.
- HEAD establishes reachability. A successful HEAD, 405 or 501 schedules a separately policy-checked GET. Only `get_board_schema` authorizes publication or a spelling update. The expected public GET response can contain zero jobs. Historical HEAD-only confirmations are queued for GET, and publication labels already projected HEAD-only evidence `pending_get`. Existing inventory polling handles job records, completeness and closure.

## Publish to Prospect's registry

These commands are offline:

```sh
python -m tools.discover_boards --data-dir "$PROSPECT_DATA_DIR" status --campaign ats-20261114
python -m tools.discover_boards --data-dir "$PROSPECT_DATA_DIR" publish \
  --registry "$PROSPECT_DATA_DIR/ats-registry.json"
```

Publish to the same registry path passed to the existing `tools.expand_ats` workflow. Publication is an idempotent, atomic merge of verified boards into the v1 document. Publishers and the existing registry workflows coordinate through a file lock beside the registry, including when publishers use separate SQLite stores. Each published board carries verification metadata and a pointer to its first eligible source observation in the local discovery store. The store holds additional evidence. Publication preserves source and poll metadata. The scheduler is single-process per SQLite store.

Fixture coverage includes 5,050 verified boards through publication. Live source reachability, archive cursor behavior and coverage, endpoint status/schema behavior, and sustainable throughput still need measurement. Archive and scan coverage is partial by nature. The discovery totals do not estimate the number of open jobs or market recall.
