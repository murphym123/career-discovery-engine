# DC Career Discovery Engine

Discovery-first job search across unfamiliar employers in Washington, DC, the
nearby DMV and suitable US-remote work. Broad feeds and open-web discovery are
primary; no employer allowlist is required. Scores are triage heuristics and do
not establish qualifications, job availability or interview probabilities.

## New: optional discovery of unfamiliar public ATS boards

The [Issue #21 iCIMS increment](docs/issue21/ASSESSMENT.md) adds robots-aware
public iCIMS search inventories, observed regional search-form options and
stable posting identities. It repairs `US-DC` / `US-DC-Washington` aliases
without widening the nearby-DMV market. iCIMS links can come from existing
sources, employer career pages and a bounded free URL-index query; there is no
employer allowlist or API key. Shells, restrictions and partial pagination remain
visible in the source ledger. Regional descriptions use the existing shared
budget and all uninspected records remain in the full audit.

The measured gain was modest: **6 → 12** regional ATS records with descriptions
in an equal-budget fresh-registry comparison. Three separate diagnostic boards
yielded **14** described regional labels; those direct tests are not all automatic
discoveries or verified early-career vacancies. See the assessment for exact
budgets, provenance, qualification evidence and remaining source-discovery gaps.

The [Issue #17 production fix](docs/issue17/ASSESSMENT.md) adds per-source
red/yellow/green health, balanced polling across Greenhouse/Lever/Ashby/JazzHR,
isolated oversized-response failures and employer-spread regional description
retrieval. The manual workflow can finish with a prominent **PARTIAL COLLECTION**
warning when useful records survive; it does not turn source failure into a
zero-vacancy result. All-source failure remains an error. Standalone collection
keeps strict exit behavior unless `--allow-partial` is explicitly supplied.

`dc_detail_budget` still caps original-source detail requests at 0–30 each. In
expanded discovery it supplies a shared base detail budget, with up to that many
additional slots for remaining regional metadata (maximum 60 shared pages).
Board indexes are polled first; the existing total request/byte/time limits
still apply. `ats-discovery/ats-source-audit.json` records selected/attempted
boards, unattempted reasons, platform counts and per-board local description
denominators. `collection-status.json/.md` summarize original and combined health
independently of ranking. No new credentials are needed.

Issue #14 extends this path with advertised public JazzHR sitemap seeds and
hosted job inventories, an Impact Opportunity RSS adapter, and passive discovery
of RSS/Atom, sitemap, career-site and ATS publication links. These are publication
mechanisms, not an employer allowlist. The independent coverage ledger records
discovered, searched, partial, inaccessible and not-yet-covered sources, with
explicit inventory denominators where available.

Read the [current coverage assessment](docs/issue14/ASSESSMENT.md),
[actual recovered examples](docs/issue14/RECOVERED_EXAMPLES.md),
[measured comparison](docs/issue14/metrics.json), and
[small interface boundaries](docs/issue14/INTERFACES.md). The bounded DC test
recovered meaningful additional employers, but also unpaid appointments, pools
and specialist work; it does not measure the whole employment market.

```sh
# Existing manual DC path; no new credentials. Both commands are manual.
python -m tools.audit_dc_sources --output output/dc-source-audit.json
python -m tools.expand_ats --base output/dc-source-audit.json --strategy regional

# Separate portable cold start, without the DC source fixture or seed employers:
python -m tools.discover_publications --city Tampa --state FL --output-dir output/tampa
```

In GitHub, select **source_mode=dc**, **expanded_ats=true**, and keep
**allow_paid_search=false**. The existing experimental ranking remains optional
and separate. The report artifact includes `coverage-audit.*`, `ats-discovery/`,
the original five-source audit, and the combined unranked collection. Registry
artifacts retain bounded seen identities, source status history and discovery
cursors across manual runs. Missing/expired artifacts reset that history.
The expanded manual GitHub path now permits three public employer-link checks,
including the optional DC-HQ directory; the CLI remains off unless
`--employer-budget` is supplied. These checks found no new boards in the paired
pilot and remain bounded by the existing request limits. They are not a
prerequisite for source-link discovery. No AI evaluator or schedule is added.

The manual DC workflow can discover employer boards from free public URL indexes
and links in source evidence, then read documented Greenhouse, Lever and Ashby
APIs, plus permitted public JazzHR and iCIMS HTML inventories. The registry starts empty; there is no employer allowlist or tenant-name
guessing. This is a bounded pilot with material source and relevance gaps.

Enable **expanded_ats** (default false) in `source_mode=dc`; leave
`allow_paid_search=false`. `ats_board_budget` defaults to 30. Original five-source
JSON/CSV/Markdown remain unchanged, alongside ATS provenance and a complete
combined unranked audit. Optional experimental ranking reads the combined audit
and remains unvalidated. No new credentials or schedules are needed.

Read the [issue #11 coverage assessment and setup](docs/issue11/ASSESSMENT.md),
[actual requirements review](docs/issue11/REVIEW_SAMPLE.md), and
[measured results](docs/issue11/metrics.json). Regional counts are separated from
remote/global volume, possible duplicates, pools and specialist requirements.

## Current: optional experimental ranking in the manual GitHub workflow

The manual DC workflow preserves its full unranked audit. After this change is
merged, an explicit **experimental_ranking** checkbox (default **false**) adds
an **EXPERIMENTAL — NOT INDEPENDENTLY VALIDATED** shortlist, full ranked audit
and review queue to the same report artifact. It ranks the already retrieved
JSON with zero extra source or model calls. Other modes and paid-search guards
are unchanged; no schedule is enabled.

Read the [issue #9 assessment and cloud instructions](docs/issue9/ASSESSMENT.md).
A genuinely new 40-record diagnostic finds more promising cases but many false
positives. A separate 40-record original-text packet remains blinded, unlabeled
and outside development/tests for later independent review. No human reviewer
is currently available; **the ranking system is not validated**. The unranked
audit remains the source of truth.

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
2. Keep `source_mode=dc` and `allow_paid_search=false`. Leave `experimental_ranking=false` for only the full unranked audit, or explicitly check it to add the unvalidated ranking. Choose `free` explicitly for the earlier remote/registered-source mode.
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


### Issue 23 foundation behavior

Greenhouse lists request `content=true`; oversized bodies fall back to the metadata index and existing bounded detail allocation, with explicit partial status. See [measured results and limits](docs/issue23/RESULTS.md).

Expanded manual DC regional runs also check the existing USAJOBS collector. Configure repository secrets `USAJOBS_API_KEY`, `USAJOBS_USER_AGENT` (registered email), and repository variable `USAJOBS_TERMS_ACCEPTED=true` only after confirming authorized use and terms. Missing configuration is reported as `configured=false` / `not_collected`; no request is made. The original five-source audit is preserved; federal rows appear separately in regional/combined artifacts.

Registry state is staged and verified before restore, written atomically, and saved only after pipeline success. Explicit partial collection can still be a valid state snapshot. Inspect `registry-restore.json` for provenance or restore failures. Artifacts still expire after 30–90 days; history is not durable cloud storage.


### Bounded employer-first DC pilot

`python -m tools.freeze_employer_frame --output-dir work/dc-frame --size 30` freezes a non-personal Open Data DC certified-business frame and a deterministic development batch. `python -m tools.discover_employers --frame work/dc-frame/batch.json --registry data/ats-registry.json --output-dir output/employer-discovery` resolves observed official-site and hiring links, records unknown/unsupported channels, and hands verified supported boards to existing collectors. An optional `--base` preserves the original audit and writes a separate combined capture. No workflow mode or schedule is changed.

See [usage and input schema](docs/issue25/USAGE.md) and [actual results and limits](docs/issue25/RESULTS.md). The measured 30-employer batch corroborated 16 sites but found no supported ATS source; unconventional channel evidence is retained as unknown inventory, not zero jobs or proof of market recall.

### Manual House Employment Bulletin intake

Use `python -m tools.import_house_bulletin bulletin.pdf --output-dir output/house-bulletin` with Poppler installed. The same command accepts an exported `.eml` with attached PDFs. It retains originals, numbered source spans, completeness diagnostics and an annual vacancy ledger. See [usage](docs/issue28/USAGE.md) and [historical validation](docs/issue28/RESULTS.md).
