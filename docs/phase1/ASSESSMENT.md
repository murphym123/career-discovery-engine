# Issue #3, Phase 1: genuinely DC-oriented discovery

This change demonstrates actual regional source yield before making any ranking
changes. It adds two sources that discover across offices and organizations,
without an employer allowlist: the Senate Employment Bulletin and Union Jobs
Clearinghouse's public RSS plus DC/Maryland/Virginia indexes.

**Read the [15-record review sample](REVIEW_SAMPLE.md) first.** It contains real
retrieved records, location evidence, employers, duties, experience requirements,
employment terms where stated, and reasons to be cautious. It is unranked and
includes negative controls. It is not a list of 15 suitable or confirmed-open
vacancies. [Machine-readable sample](review-sample.json) includes exact short
requirements excerpts. [All 81 captured identities and aggregate evidence](real-records.json)
separate source facts from review conclusions.

## Actual observations

The bounded audit ended at `2026-11-05T06:55:18Z`. A further six explicit
requirements checks ended at `2026-11-05T06:57:58Z`. These are UTC observation
times; the underlying board dates are retained verbatim.

| Observation | Senate | Union Jobs | Total |
|---|---:|---:|---:|
| Raw index/feed sightings | 25 | 101 | 126 |
| Distinct source identities emitted | 25 | 56 | 81 |
| Public descriptions observed, including selected checks | 21 | 25 | 46 |
| Initial HTTP requests, including robots | 22 | 25 | 47 |

The six selected checks used eight further HTTP requests, including two robots
reads, without refetching indexes. Initial retrieval and selected detail checks
had no errors. Separate exploratory source/policy probes are not included in the
47+8 audit-request count. No paid API requests were made.

The existing geographic parser labels 54 records DC/DMV, 22 elsewhere, four
remote with uncertain eligibility, and one unknown. Those are **unchanged parser
labels**, not independently verified worksite counts. The review sample exposes
a nearby Camp Springs, Maryland role it misses. Some regional indexes include
national searches or jobs outside the region. One captured record is an ongoing
résumé pool, explicitly identified as not a discrete vacancy. There are 42
distinct non-confidential board employer labels; labels are not a verified count
of separate legal employers, and prior familiarity to the user was not assessed.

No claim of comprehensive coverage, labor-market recall, candidate qualification
or current acceptance of applications follows from these counts. No employer-side
vacancy availability was independently confirmed. The normalized source metadata
and short experience excerpts are committed; full live descriptions remain in
the private/local audit rather than being redistributed as fixtures.

## Source choices and access

| Source | Implemented access | Evidence and limitations |
|---|---|---|
| Senate Employment Bulletin | Public rendered [DC listing page](https://careers.employment.senate.gov/l-district-of-columbia-jobs.html), then bounded same-site job details | [Official source description](https://employment.senate.gov/job-vacancies/) and [FAQ](https://careers.employment.senate.gov/faq). No account/key needed to read. The tested rendered page contains 25 cards; its “Load more” is client-driven. The adapter does not call disallowed search/XML-feed endpoints or private APIs, so it does not claim the complete bulletin. |
| Union Jobs Clearinghouse | Advertised [public RSS](https://www.unionjobs.com/rss.php), plus public regional indexes for [DC](https://www.unionjobs.com/location.php?state=District%20of%20Columbia), [Maryland](https://www.unionjobs.com/location.php?state=Maryland) and [Virginia](https://www.unionjobs.com/location.php?state=Virginia) | No key/login. RSS holds the newest 50 national postings; only explicit DC/MD/VA geographic tags are retained, and older regional listings are recovered from the three indexes. A state tag never proves a DC worksite. IDs merge feed/index sightings, preserving index provenance. These are labor-sector and adjacent think-tank/association/consulting listings, not a general remote feed. |

Both adapters check robots rules; absent rules at HTTP 404 are treated as absent,
whereas unreadable/denied rules stop retrieval. They wait one second before each
page request, cap details at 0–30 per source, bound page size, validate same-site
HTTPS listing paths and redirects, and stop on 403/429 without retry. A failed
detail never erases the index record. There is no login, application action,
CAPTCHA bypass, undocumented search API, employer enrollment, or employer list.

Public Affairs Council and Daybook were investigated and left **manual-only**:
their [terms](https://pac.org/terms-of-use) and
[terms of service](https://www.daybook.com/terms-of-service), respectively,
restrict automated access. No adapter for either was enabled. ASAE presented a
bot challenge, Philanthropy Jobs returned 403, and the tested legacy Work for
Good regional URL returned 404; none is counted as implemented coverage. The
House's generic résumé-bank titles are not individual vacancies; no mailing-list
subscription or account action was taken to obtain its weekly bulletin.

## What the actual sample demonstrates

- **Legislative and research work:** a Senate foreign-policy legislative aide;
  the Joint Committee on Taxation's legal research role. Relevant functions do
  not eliminate Hill-experience, database or subject-matter requirements.
- **Programs/operations and executive support:** Senate staff/executive
  assistants and AFL-CIO executive administration. The Senate executive role has
  an explicit two-year minimum; other listings lack a numeric minimum and should
  not be relabeled “zero experience required.”
- **Think-tank/public-policy adjacency:** EPI's congressional fellowship has a
  stated 1–2-year labor-movement requirement. Its existence demonstrates source
  coverage, not that the candidate possesses that background.
- **Nontraditional entry route:** UFCW Local 400's temporary organizer-in-training
  opportunity has paid compensation, a vehicle requirement and irregular hours;
  related retail/service experience is optional.
- **Association/member and legal administration:** an ATU legal secretary,
  PGCEA executive assistant and MSEA membership assistant. Some are at three
  years or have degree/experience alternatives and specialized software needs.
- **Negative controls:** a senior financial-policy role with eight/twelve-year
  requirements; an ongoing résumé pool; an ambiguous Maryland worksite; and an
  outer-region administrative role with a five-to-eight-year preference. These
  prevent counting every sector-relevant title as suitable DC entry-level work.

The usual candidate target remains roughly 0–2 years. The issue's wider 0–3-year
review band is used to inspect boundary cases, explicitly marked in the sample.
No new competitive-advantage claims or equivalencies are invented.

## Side by side with the prior source result

| Evidence | Prior run documented in issue #3 | Phase 1 source audit |
|---|---|---|
| Retrieval universe | Remotive and Jobicy | Senate offices + Union Jobs RSS/regional indexes |
| Captured records | 610 | 81 distinct source identities |
| DC-specific evidence | Issue reports zero DC-specific records; all labeled US remote | Actual location text on regional cards and 46 observed public descriptions; examples linked in sample |
| Evidence of experience requirements | Issue reports generic-token false positives and large review queues | 15 human-reviewed records distinguish numeric requirements, preferences, alternatives, unknowns and domain-specific gaps |
| Recommendation quality | Issue reports clinical/security/procurement false positives | Not optimized or claimed improved; this phase's default GitHub DC mode is an unranked audit |

The prior values come from [issue #3](https://github.com/murphym123/career-discovery-engine/issues/3)
and its linked live workflow, not a rerun of remote APIs. The source universes,
budgets and times differ, so this is a coverage/evidence comparison, not a
controlled performance experiment. No general remote feed was added or called
to inflate Phase 1 counts.

Scoring, shortlist selection, seniority/specialist holds and geographic logic are
unchanged. Existing false positives/negatives therefore remain. The source
sample explicitly preserves a nontraditional organizer role, legal administration
and a Camp Springs geography miss so subsequent work can assess them. Ranking
precision, rejection audits at market scale and competitive-fit scoring are
deferred to later phases; issue #3 remains open.

## Run and reproduce

The manual GitHub discovery workflow now defaults to `source_mode=dc`, which
runs the unranked audit and publishes Markdown, CSV and JSON artifacts. It does
not activate a schedule or run a search when the PR is opened. Existing `free`,
provider and explicitly opted-in Brave modes remain available.

```sh
python -m pip install -r requirements.txt
python -m tools.audit_dc_sources --max-details 20 --output output/dc-source-audit.json
```

To inspect an explicit stratified set of previously observed IDs without
repeating the source indexes:

```sh
python -m tools.audit_dc_sources --from-capture output/dc-source-audit.json \
  --detail-ids unionjobs:31609,senate:1279 --max-details 2 \
  --output output/dc-reviewed-audit.json
```

IDs must be present in the capture, and page budgets are enforced. This selects
source-evidence checks, not ranked recommendations. Live output will change or
lose listings; an old ID's absence is not evidence of an implementation failure
or permission to bypass access restrictions.

The main engine also supports `--sources dc` / individual `senate,unionjobs` and
retains provenance in its all-candidate audit. That path still uses the old,
unvalidated heuristic ranking. Use the unranked tool for Phase 1 assessment.

No new credentials are required. Existing optional registered adapters still
need user-owned `MUSE_API_KEY`, approved `RELIEFWEB_APPNAME`, or both
`USAJOBS_API_KEY` and `USAJOBS_USER_AGENT`. No keys were created, borrowed or
activated for this phase; no authenticated yield is claimed. Do not treat the
presence of adapter code as proof of coverage until it has a real-record test.

## Tests and evidence preservation

`python -m unittest discover -s tests -v` passes **57 tests**, preserving all 41
previous tests. New regression fixtures are minimized fragments of actually
retrieved Senate/Union Jobs HTML and RSS. Their manifest records source URLs,
HTTP observations, original-content hashes, transformations and fixture hashes.
They are clearly separated from the older synthetic fixtures.

The tests exercise authentic employer/location/experience parsing, geographic
RSS filtering, ID deduplication, regional provenance, detail budgets, public
description status, closed notices, robots/403/429 behavior, schema changes,
size limits, network failures, safe redirects and default DC integration. The
15-record sample is checked against the 81 captured identities. AST/content
comparison confirms the scoring/selection functions and screening/geography
module match the baseline. Local Python 3.13.2; CI uses Python 3.11.

## What remains missing

The adapters have meaningful political/labor/public-policy yield, with a strong
sector bias. They do not establish broad consulting, corporate government affairs,
all think tanks, philanthropy, universities, House offices, all associations, or
state/local-government coverage. EPI in one sample is not evidence of general
think-tank coverage. Senate retrieval is limited to the rendered batch; Union
Jobs does not cover every union or nonprofit and regional results are noisy.
No assertion of employer novelty to the user, active vacancy status, full-time
paid status where unstated, or candidate suitability is made.

Further Phase 1 work can add documented, permitted specialty feeds or genuinely
DC-oriented registered-source tests, with the same real-record evidence
standard. Source breadth should expand before adjusting weights or making
recommendation-quality claims. Employer monitoring remains supplementary.
