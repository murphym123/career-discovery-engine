# Discovery coverage assessment

Assessment date: November 4, 2026 (America/Denver). Baseline:
`f1ce56c5cf4fdebeb490a447cc73e89a04037f2e`. This report distinguishes synthetic
test records, actual feed responses and independently verified vacancies. **No
vacancies were independently verified, and no market-wide recall is claimed.**

## Existing system audit

The deployed repository had one free structured adapter (Remotive), optional
Brave search, CSV import, SQLite history, a five-lane scoring model, shortlist
diversity caps and optional robots-aware JobPosting extraction. Discovery was
already the architectural goal; there was no employer allowlist to replace.

The free default was a remote-only board. The same first six of 16 search queries
ran on every default Brave invocation, representing four families under the new
ten-family taxonomy: research, government affairs, operations and communications.
Policy, advocacy, consulting, development, executive support and unrestricted
exploration were absent from that default query prefix. Other weaknesses:

- No additional structured adapters or pagination were deployed.
- Company/title/location identity could collapse different requisitions; the
  same canonical URL with different metadata could also appear twice.
- Low-score records survived in the CSV audit, but there was no distinct
  score-independent review queue. The Markdown report showed at most 12 rejects
  ranked by the same heuristic that had rejected them.
- Any location containing “Washington” received a local bonus, including Seattle.
- Generic analyst/operations words could elevate technical and senior roles.
- Provider closing dates were not considered; source setup, caps and yield were
  not separately reported.

## Sources implemented and their boundaries

| Source | Discovery scope | Access and bounds | Evidence / live status |
|---|---|---|---|
| Remotive, retained | All job categories on one remote board | No key; one request; source attribution retained | [Official documentation](https://github.com/remotive-com/remote-jobs-api); HTTP 200, 11 records |
| Jobicy, added | All categories of US-eligible remote jobs, without title filters | Free public endpoint, no authorization header; 100/page; cursor pagination; seven-day feed window | [Official API documentation](https://jobicy.com/jobs-rss-feed); HTTP 200, 100 records with more pages |
| The Muse, added | Broad roles across participating employers, including DC-area on-site jobs | Free registration key required beyond testing; 20/page; sweep DC, Arlington, Alexandria, Bethesda, Silver Spring and flexible/remote partitions before later pages | [Official API documentation](https://www.themuse.com/developers/api/v2); one anonymous integration-test request, HTTP 200, 20 DC-search records |
| ReliefWeb, added | US humanitarian/nonprofit jobs across categories; supports unfamiliar organizations | Approved appname; full structured fields; 100/page with offsets; US country filter, local geography reviewed afterward | [Read/search](https://apidoc.reliefweb.int/read/), [jobs schema](https://apidoc.reliefweb.int/read/resources/jobs/); fixture-tested only, approved appname not configured |
| USAJOBS, added | All federal job families within a 35-mile radius of DC, without grade/title filters | Free key and registered email; 100/page; no assumed citizenship/hiring-path eligibility | [Search reference](https://developer.usajobs.gov/api-reference/get-api-search), [authentication](https://developer.usajobs.gov/guides/authentication); fixture-tested only, credentials not configured |
| Brave, retained and diversified | Open web, including employers absent from structured feeds | Requires explicit cost opt-in and key; 1–20 queries, at most 20 hits each; never part of `free` | No Brave calls made; query planner and mocked retrieval tested offline |
| CSV, retained | Any responsibly collected supplied records | Local import; no external request | Original pipeline test retained and passing |

Default `free` selects Remotive and Jobicy, plus only those of Muse, ReliefWeb
and USAJOBS with configured credentials. The maximum at default page caps is
25 requests across all five sources; without credentials it is at most seven.
Caps are per source, so a six-request Muse sweep is not six pages per city.
No employer IDs, employer enrollment or saved employer lists are necessary.
These integrations discover within each provider's universe; they do not create
an independent index of the whole web.

Arbeitnow was evaluated and not added: its documented free API primarily covers
Europe, a poor DC fit ([provider explanation](https://www.arbeitnow.com/blog/job-board-api)).
Employer-specific Greenhouse/Lever boards were not substituted for broad
discovery. Paid aggregators, paid search requests and commercial Jobicy direct
links were not used. The no-cost design favors registered broad sources rather
than treating unverified or inaccessible specialty boards as implemented coverage.

## Query diversification and unusual opportunities

Ten query families cover research, policy, government affairs, advocacy,
consulting, communications, operations, development, executive support and
unrestricted exploration. OR alternatives reduce exact-title dependence. The
same six-query budget explores six families initially and all ten over two
consecutive history-backed runs. The old prefix represented four families over
both runs. Later blocks cover other DMV locations, US remote and alternative
wording. This is measured **query-plan diversity**, not search-result recall.

Structured retrieval has no title, grade, seniority or career-category keyword
gate. All normalized records enter the audit before screening. Unusual roles
such as the synthetic “Democracy Fellow” and “Partnerships Navigator” survive and
appear in a separate deterministic review sample even below the scoring cutoff.
The default sample has five slots, independent of the recommendation count.
High-scoring senior/specialist titles are routed to explicit review rather than
recommended automatically. These flags can be wrong (for example, a junior
“manager”); all flagged records remain inspectable in the full audit. The sample
is finite and does not guarantee every atypical role gets prominent placement.

## Measured comparisons

### Actual feed responses, separate from test fixtures

Three read-only public requests succeeded. The same captured first responses
were replayed locally through the old and new runners; replay made no additional
network requests. Neither relevance nor current availability was independently
verified. The no-key comparison used only the two public remote feeds. The Muse
column used a single anonymous response permitted for integration testing; it
does not imply registration was completed for ongoing use.

| First-response replay metric | Old Remotive-only | New, no credentials | New, plus Muse test response |
|---|---:|---:|---:|
| Records retrieved and normalized | 11 | 111 | 131 |
| Unique records retained | 11 | 111 | 131 |
| Heuristic shortlist | 0 | 7 | 8 |
| Lanes in shortlist | 0 | 4 | 4 |
| Separate exploration sample | 0 | 5 | 5 |
| Senior/specialist records held for review | 0 | 70 | 88 |

These are observed counts from bounded responses, not a controlled measure of
market coverage or a count of suitable vacancies. Remote feeds skew toward
technology and senior roles; the review counts make that limitation visible.
The Muse reported 400 DC pages, but this was provider metadata, not a traversal
or validated total. Raw live descriptions were not committed. Aggregate
results, timing and limitations are in [live-check.json](live-check.json).

### Synthetic fixture benchmark

Fixtures follow documented provider shapes and contain entirely invented
employers, reserved example.org URLs, realistic HTML descriptions, missing data,
cross-board duplicates, same-title requisitions, unusual titles, non-DC locations
and expired dates. **None is a real vacancy.** Sixteen synthetic job identities
are manually marked relevant to this test universe. The benchmark executes the
actual baseline module and the current runner, with HTTP fully intercepted.

| Synthetic retrieval metric | Old free default | New, no credentials | New, all five free sources configured |
|---|---:|---:|---:|
| Sources exercised | 1 | 2 | 5 |
| Intercepted requests | 1 | 3 | 11 |
| Emitted records | 3 | 7 | 27 |
| Unique records | 3 | 5 | 21 |
| Labeled relevant identities retained out of 16 | 2 | 3 | 16 |
| Heuristic recommendations | 0 | 3 | 14 |
| Distinct recommended lanes | 0 | 3 | 6 |

The configured-source result demonstrates wiring and behavior against the test
universe, not real-world recall. It assumes approved free credentials; it must
not be presented as the result available without setup. The old free fixture
shortlist was empty partly because “USA” in a remote location was not recognized
by its geographic heuristic, in addition to its limited source universe.

To isolate normalization, identity and screening, both implementations also
received exactly the same 27 normalized records via CSV:

| Same-input metric | Old | New |
|---|---:|---:|
| Unique records after deduplication | 23 | 21 |
| Relevant fixture identities retained | 15 / 16 | 16 / 16 |
| Distinct same-title DC requisitions preserved | 1 / 2 | 2 / 2 |
| Relevant fixture identities recommended | 13 | 14 |
| Recommended lanes | 4 | 6 |
| Washington-state records incorrectly recommended | 1 | 0 |
| Expired-by-date records incorrectly recommended | 1 | 0 |
| Distinct atypical titles surfaced in exploration | 0 | 2 |

Fewer unique records here reflects merging shared canonical URLs while retaining
distinct provider IDs, not indiscriminate title deduplication. Different URLs
and different board IDs can still represent the same job; uncertain matches are
intentionally retained. Jobicy's 100-row synthetic pagination scale test also
retrieved 300 rows over three pages and retained 280 distinct identities after
overlap removal. [benchmark.json](benchmark.json) contains the full measurements,
baseline and fixture hashes and taxonomy notes.

## Executable verification

`python -m unittest discover -s tests -v` passes **41 tests**, including all 14
unchanged original tests. The new tests cover provider schemas, three pagination
styles, page caps, 300-row retrieval, malformed records, no-key behavior,
credential redaction, redirects/HTTP failures, rate limits, retained earlier
pages, provenance, requisition preservation, date/geography/seniority screening,
query rotation, saved history, report labels and benchmark reproducibility.
CI runs the same offline suite on Python 3.11; local execution used Python 3.13.2.
The original pipeline test emits an existing unclosed-file ResourceWarning on
Python 3.13; it passes and was left unchanged.

Reproduce the controlled comparison from a clone containing the baseline:

```sh
git show f1ce56c5cf4fdebeb490a447cc73e89a04037f2e:career_engine/__main__.py > /tmp/career-baseline.py
python -m tools.benchmark_discovery --baseline-engine /tmp/career-baseline.py --output docs/benchmark.json
```

The synthetic benchmark is reproducible. The live counts are a point-in-time
observation; a fresh live request will change, and only three public endpoints
were checked. ReliefWeb/USAJOBS require a later authenticated smoke test after
approved free setup. No undocumented or borrowed credential was used.

## Remaining gaps and appropriate next steps

This does not comprehensively cover the DC labor market. Missing or partial
segments include Hill offices and committees, small policy shops and think
tanks, lobbying and public affairs firms, associations, local/state government,
specialty nonprofits and employers absent from the participating feeds. Closed
networks, newsletters, PDFs, login-only boards and pages without JobPosting
metadata remain difficult. Remote eligibility, degrees, required experience,
clearances and citizenship need human review. A published record can be stale.

Recommendations remain heuristic, with title flags and geographic parsing that
can generate false negatives. Foreign-only remote roles, unknown locations and
country-level listings are reviewed conservatively; this is not a complete
eligibility engine. No applied-history deduplication was added. Shortlist caps
and bounded extraction can omit good jobs from the visible shortlist; the full
audit preserves normalized records. HTML extraction remains the original
bounded metadata reader, not a full web crawler.

The practical next step is free registration for Muse, ReliefWeb and USAJOBS,
followed by small manual authenticated smoke tests and human labeling of actual
results by lane, employer novelty, geography and experience requirements. That
would support a real relevance study. Specialty feed additions should follow
documented access and demonstrated local yield; monitoring known employers
should remain supplementary. No schedule or automated application is needed.
