# DC Career Discovery Engine

Discovery-first job search across unfamiliar employers in Washington, DC, the
nearby DMV and suitable US-remote work. Broad feeds and open-web discovery are
primary; no employer allowlist is required. Scores are triage heuristics and do
not establish qualifications, job availability or interview probabilities.

## Current: evidence-backed candidate triage (diagnostic)

Use the new source-independent offline evaluator after broad retrieval:

```sh
python -m tools.rank_jobs --input output/dc-source-audit.json --output output/ranked-brief.md
```

It separates career relevance, description-supported qualifications, transferable
experience, potential advantages and uncertainty. Missing descriptions stay in
investigation; every input row remains in JSON/CSV. Detailed analysis defaults
to 200 descriptions with score-independent exploration. No credentials, AI,
network calls or paid services are needed for ranking. Optional integration into
the existing runner: `--triage-output output/ranked-brief.md`.

Start with the [Phase 3 assessment](docs/issue7/ASSESSMENT.md) and
[example ranked report using actual-record abstracts](docs/issue7/EXAMPLE_RANKED_REPORT.md).
The original-text comparison finds more labeled promising top-30 records but
**does not establish a precision improvement**. The initial holdout exposed bugs
and was used for corrections, so final results are diagnostic, not untouched
holdout validation. Human review of full requirements and posting status remains
necessary. Existing score/selection functions remain available for compatibility
and paired baseline measurements.

```sh
python -m tools.benchmark_triage --stress --report output/issue7/ranked-brief.md
python -m unittest discover -s tests -v
```

## Broad DC discovery and description inspection

The unranked audit now covers **Senate, Union Jobs, Global Jobs, On Think Tanks,
and Work for Nonprofits**, without an employer allowlist or paid credentials.
Start with the [27-record actual DC/DMV review](docs/issue5/REVIEW_SAMPLE.md) and
[issue #5 assessment](docs/issue5/ASSESSMENT.md). The review distinguishes
plausible routes, stretches, ineligible roles and missing evidence.

```sh
python -m tools.audit_dc_sources --max-details 20 --inspection-seed 5 --output output/dc-source-audit.json
```

Description inspection uses bounded early-career signals, employer/function and
title variety, plus a seeded exploratory stratum. Every index record remains in
the audit. Provider labels and titles are retrieval hints, not qualifications.
The audit defaults to all five sources; its default seed is 0. Choose `--sources`
to restrict boards, or `--inspection-strategy prefix` for a controlled comparison.
The manual GitHub `dc` mode runs this broad unranked audit.

The main engine accepts `--sources dc-broad` and individual new source names.
Its legacy `dc` alias remains Senate+Union Jobs for compatibility. The legacy fit scores remain available; Phase 3 adds the separate evidence
triage above. State-only locations remain ambiguous.

## Phase 1: original DC source evidence

Start with the [15-record actual opportunity review](docs/phase1/REVIEW_SAMPLE.md)
and [Phase 1 assessment](docs/phase1/ASSESSMENT.md). The new no-key sources are the
Senate Employment Bulletin and Union Jobs Clearinghouse's advertised RSS plus
DC, Maryland and Virginia indexes. They discover across offices/employers,
without an employer allowlist. No general remote feed was added for this phase.

```sh
python -m pip install -r requirements.txt
python -m tools.audit_dc_sources --sources senate,unionjobs --max-details 20 --output output/dc-source-audit.json
```

This produces an **unranked** Markdown/CSV/JSON source audit. It records actual
location text, provenance, detail-access status and public descriptions where
retrieved. The manual GitHub workflow now defaults to this `dc` mode. No new
credentials are needed. Detail requests are capped at 0–30 per source, with
robots checks, one-second page pacing, strict source URLs and no bypass/retries
on access denials or rate limits. Details can be unknown; board presence is not
confirmation that the employer still accepts applications.

The main runner also defaults to `--sources dc`, accepts individual `senate` and
`unionjobs` sources, and retains their evidence metadata in its full audit. Its legacy ranking remains available; use the Phase 3 evaluator for evidence
triage and the unranked audit for source assessment. The measured sample contains adjacent
roles, specialist/experience gaps, location ambiguity and a résumé-pool control,
not a claimed shortlist of suitable vacancies.

## Other free APIs and remote feeds (opt-in)

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
2. Keep `source_mode=dc` and `allow_paid_search=false` for an unranked DC source audit; choose `free` explicitly for the earlier remote/registered-source mode.
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
DC labor-market coverage. The main runner’s legacy `dc` default covers Senate and Union Jobs; the
unranked audit covers all five boards. Optional unregistered remote feeds do
not establish on-site DC coverage. Hill, association, consulting, advocacy,
   think-tank and small-employer vacancies often remain outside these feeds.

## Validation and measurable comparison

```sh
python -m unittest discover -s tests -v
git show f1ce56c5cf4fdebeb490a447cc73e89a04037f2e:career_engine/__main__.py > /tmp/career-baseline.py
python -m tools.benchmark_discovery --baseline-engine /tmp/career-baseline.py --output docs/benchmark.json
```

All tests run offline. Early discovery fixtures are explicitly **synthetic**
and do not represent real vacancies. Later source fixtures and Phase 3 listing
metadata derive from actual retrieved postings, with provenance and representation
labels; synthetic stress expansions do not represent additional vacancies. The benchmark runs the actual prior
implementation and current runner on controlled fixtures. [Coverage assessment](docs/COVERAGE.md),
[fixture measurements](docs/benchmark.json), and [live connectivity/replay counts](docs/live-check.json)
separate test recall from real feed retrieval; neither measures market recall.
