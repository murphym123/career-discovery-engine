# DC Career Discovery Engine

Discovery-first job search across unfamiliar employers in Washington, DC, the
nearby DMV and suitable US-remote work. Broad feeds and open-web discovery are
primary; no employer allowlist is required. Scores are triage heuristics and do
not establish qualifications, job availability or interview probabilities.

## Free discovery

```sh
python -m pip install -r requirements.txt
python -m career_engine --sources free --source-pages 6
```

`free` uses Remotive and Jobicy without credentials, and automatically includes
The Muse, ReliefWeb and USAJOBS only when their free access is configured.
It never selects Brave, even if `BRAVE_API_KEY` exists. None of the new adapters
uses a paid API, paid fallback, commercial Jobicy key, or employer list.

| Source | Setup | Coverage and limits |
|---|---|---|
| Remotive | None | Remote board; one unfiltered request, source links retained; delayed 24 hours |
| Jobicy | None | Broad US-eligible remote jobs; 100 per page, opaque cursor pagination, recent seven-day window |
| The Muse | `MUSE_API_KEY` | Registered free API; all roles, rotating DC, Arlington, Alexandria, Bethesda, Silver Spring and flexible/remote partitions; 20 per page |
| ReliefWeb | `RELIEFWEB_APPNAME` | Approved application name required; US humanitarian/nonprofit jobs, 100 per page; includes non-DC jobs for geographic review |
| USAJOBS | `USAJOBS_API_KEY`, `USAJOBS_USER_AGENT` | Free key and registered email; all federal job families within 35 miles of DC, 100 per page; eligibility needs review |

Use the providers' official setup and terms links in [the coverage report](docs/COVERAGE.md).
The Muse allows anonymous integration testing but requires registration beyond
testing. ReliefWeb requires an approved application name; do not invent or borrow
one. Missing optional setup is reported, not silently counted as coverage.

`--source-pages` caps requests **per added API**, not per employer or per location
(default 6, allowed 1–20). A configured free run makes at most 25 source requests
at the default cap: one Remotive request plus six per added API. Muse rotates
geographic partitions before revisiting later pages; six requests are only one
page per partition. Reports flag caps, failures, malformed records and source
yield. Earlier pages survive later failures. HTTP errors stop a source; there is
no automatic retry. Respect each provider's polling limits; Remotive recommends
at most four refreshes daily and Jobicy hourly. No scheduler is enabled.

## Run manually on GitHub

1. Open **Actions → DC career discovery → Run workflow**.
2. Keep `source_mode=free` and `allow_paid_search=false`.
3. Download the report artifact from the completed run.

Optional credentials belong in repository secrets with the names above. This PR
does not create credentials, alter account settings, run discovery workflows, or
activate scheduled searches. Existing `remotive`, `brave` and `both` workflow
modes remain available; Brave now requires explicit cost authorization.

## Open-web exploration (optional, may cost money)

Brave remains supported but requires `BRAVE_API_KEY` **and** the explicit CLI
`--allow-paid-search` flag (or workflow `allow_paid_search=true`). No Brave
requests were made for this change. Example, only after authorizing charges:

```sh
python -m career_engine --sources brave --allow-paid-search --query-budget 6
```

Query families cover research, policy, government affairs, advocacy, consulting,
communications, operations, development, executive support and an unrestricted
wildcard. Six-query budgets rotate through all ten families over two runs using
SQLite history, rather than repeating the same prefix. Subsequent blocks explore
DMV locations and US-remote work, then alternative wording. Use `--query-offset`
for reproducible overrides. The budget remains 1–20 queries, each capped at 20
results; diversification does not silently increase search charges.

`--max-extract 0` keeps optional HTML inspection off by default. Opt-in extraction
is capped at 30 pages, respects robots rules and URL/redirect restrictions, and
uses schema.org JobPosting metadata. Unverified search hits remain a separate
lead list, never confirmed job records. No login or anti-bot bypass is attempted.

## Review outputs and history

- `output/live_brief.md`: shortlist, unverified search leads, score-independent
  exploration queue, per-source coverage and screening reasons.
- `output/live_brief.csv`: shortlisted structured job records.
- `output/screening_audit.csv`: **every normalized unique candidate**, including
  unusual, low-score, senior/specialist, expired-by-date and off-market records.
- `output/discovery_metrics.json`: source requests/yield, caps, failures, decision
  counts and query plan. Source received counts include malformed entries;
  the run's received count includes records emitted for normalization.
- `data/live.db`: SQLite history and query rotation. Existing job history remains
  compatible; canonical URLs also identify previously seen records after identity
  changes. GitHub history artifacts expire after 90 days; reports after 30 days.

Provider IDs preserve distinct same-title requisitions. Canonical URLs merge
cross-source duplicates with provenance; uncertain cross-board matches remain
separate. The shortlist retains diversity caps. A separate default five-slot
exploration sample surfaces unusual/uncertain records below the threshold without
labeling them recommendations. Senior/specialist title flags route to review,
and the full audit permits recovery of false negatives. CSV import remains:

```sh
python -m career_engine --sources csv --import-csv candidates.csv
```

This does not synchronize Notion, submit applications, or claim comprehensive
DC labor-market coverage. Without registered sources the default covers two
remote boards, not the on-site DC market. Hill, association, consulting, advocacy,
think-tank and small-employer vacancies often remain outside these feeds.

## Validation and measurable comparison

```sh
python -m unittest discover -s tests -v
git show f1ce56c5cf4fdebeb490a447cc73e89a04037f2e:career_engine/__main__.py > /tmp/career-baseline.py
python -m tools.benchmark_discovery --baseline-engine /tmp/career-baseline.py --output docs/benchmark.json
```

All tests run offline. Fixtures are explicitly **synthetic**, use `example.org`
links and do not represent real vacancies. The benchmark runs the actual prior
implementation and current runner on controlled fixtures. [Coverage assessment](docs/COVERAGE.md),
[fixture measurements](docs/benchmark.json), and [live connectivity/replay counts](docs/live-check.json)
separate test recall from real feed retrieval; neither measures market recall.
