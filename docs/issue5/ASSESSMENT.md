# Issue #5: broader DC discovery and better description inspection

This change adds **Global Jobs**, **On Think Tanks**, and **Work for Nonprofits**
to the existing Senate and Union Jobs sources. It also replaces source-prefix
inspection with a bounded, seeded mix of early-career signals, employer/function
variety, title variety and exploratory review. It does not redesign candidate-fit
scoring. Read the [27-record actual-job review](REVIEW_SAMPLE.md) for the evidence,
including qualification gaps and unknowns, rather than interpreting volume as fit.

## What genuinely improved

### Sector breadth, using actual retrieved records

The new sources expose 91 additional source records carrying explicit DC/DMV
location labels, outside the old Senate/labor universe. They include policy
institutes, issue advocacy, philanthropy, international programs, policy media,
universities, consulting and associations. They are **not 91 verified suitable
vacancies**. Some are senior, specialized, remote-compatible, repeated across
boards, or otherwise unsuitable. All index records remain available for review.

| Source | Source identities | DC/DMV location labels | Descriptions inspected | Index only |
|---|---:|---:|---:|---:|
| Senate, retained | 25 | 25 | 20 | 5 |
| Union Jobs, retained | 56 | 33 | 20 | 36 |
| Global Jobs, new | 209 | 60 | 20 | 189 |
| On Think Tanks, new | 15 | 11 | 15 | 0 |
| Work for Nonprofits, new | 22 | 20 | 20 | 2 |
| **Total** | **327** | **149** | **95** | **232** |

Global Jobs is a global specialty board. Its non-DC records are deliberately
retained and separately classified; its 209 records must not be represented as
209 DC opportunities. On Think Tanks similarly includes four non-local records.
The inspection plan focuses the finite page budget on regional evidence without
silently deleting the rest. No generic remote-job feed was added or called.

These counts come from actual public responses captured for this task, then
replayed on a fixed index snapshot to test the planner without repeatedly
requesting pages. The first complete capture used 109 HTTP requests including
robots. Fifteen newly selected pages were retrieved during planner validation,
giving 124 distinct recorded responses (123 HTTP 200, one robots HTTP 404).
Exploratory source/policy probes are outside that count. The final coherent replay
uses 109 logical reads, most from that local cache; it is not 109 further network
requests. All selected descriptions in the measured final plan were actually
retrieved. Full responses/descriptions stay private/local; committed evidence
contains factual metadata, hashes and short excerpts.

There were 581 raw sightings across feeds/indexes and 327 source identities.
Feed/index ID deduplication removed 254 redundant sightings. **Source identities
are not unique vacancies.** Obvious likely cross-board overlaps include AAUP's
federal-lobbying and state-policy roles in Union Jobs/Work for Nonprofits. Two
On Think Tanks URLs also appear to describe the same BPC tax-policy vacancy.
Those uncertain cross-board/URL matches are retained, not counted as proven new
vacancies or merged by title alone.

### Better evidence exposure at the same inspection budget

The comparison holds the actual indexes and cap fixed: 20 descriptions per
source, or every record when fewer than 20 exist. The old strategy takes the
source prefix. The new plan uses the same 95 total slots and seed 5.

| Source | DC/DMV descriptions selected: prefix → new | Employer labels: prefix → new | Function buckets: prefix → new |
|---|---:|---:|---:|
| Senate | 20 → 20 | 8 → 9 | 4 → 4 |
| Union Jobs | 16 → 20 | 12 → 16 | 6 → 5 |
| Global Jobs | 5 → 20 | 20 → 20 | 4 → 6 |
| On Think Tanks | 11 → 11 | 10 → 10 | 4 → 4 |
| Work for Nonprofits | 18 → 20 | 17 → 17 | 6 → 6 |
| **Total regional descriptions** | **70 → 91** | — | — |

This is **inspection exposure**, not recommendation precision, market recall or
a hiring-probability estimate. The employer figures are distinct board labels,
not verified legal employers; “Confidential” is not counted as a named employer.
Function buckets are weak title-based strata. The Union Jobs function count
actually falls from six to five while employer breadth and local coverage rise;
the strategy does not improve every diversity measure.

Concrete newly inspected cases outside a first-20 prefix include:

- **Carnegie Endowment, Program Assistant, China AI Initiative:** explicitly
  entry-level, bachelor-level logistics/budget/event support, 12-month full-time
  DC hybrid appointment. China/AI background is welcomed, not required.
- **Wyss Foundation, Program Assistant—Conservation:** one to two years of
  relevant experience; policy/advocacy/conservation background and writing.
- **British Embassy, Political Support Officer:** project ownership, events,
  stakeholder coordination and administrative work; no numeric minimum found.
- **Inside Washington Publishers, Federal Policy Reporter:** the employer
  explicitly says prior experience is welcomed but not required.
- **UFCW Local 400, Organizer-in-Training**, and **ATU, Legal Secretary:**
  unconventional adjacent routes preserved beyond the Union Jobs prefix.

The planner does not pre-judge these roles as suitable. Reading descriptions also
reveals counterexamples: a provider-tagged early-career foreign-affairs role
requires an environmental-studies degree, certification and specialized work;
a Wisconsin Project research posting has nuanced language requirements; Cato's
legal fellowship requires a JD, bar admission and four post-law-school years.
Those facts cannot be inferred reliably from titles or provider level tags.

## Which new sources produced the most promising opportunities?

**Global Jobs produced the strongest demonstrated early-career/adjacent yield.**
The Carnegie, Wyss, British Embassy, policy-reporter and international-program
roles support that assessment. It also produced actual consulting evidence
(CGI's foreign-assistance strategy role), but that role requests three to six
years and domain experience. Broad entry-level consulting coverage is still not
demonstrated. MCE's operations/business-development role includes internships in
its two-to-four-year requirement and identifies Spanish as advantageous; donor,
finance and data-management requirements still need assessment.

**Work for Nonprofits adds useful but modest nonprofit/association evidence.**
J Street's relationship role spans associate-level entry routes and combines
development, events and advocacy. Pew's Evidence Project associate looks
functionally adjacent, but the board's exported description does not establish a
complete qualifications section. Georgetown's sovereign-debt research role asks
for two to four years despite a senior-sounding title, with substantial academic
and domain expectations. Many other listings are senior: the professional-
association governance role in the sample requires twelve years. This is not
proof of strong entry-level association coverage.

**On Think Tanks is valuable sector evidence but thin early-career evidence.**
The observed DC set is concentrated in Cato and includes senior fellows,
directors and specialist legal work. Cato's courses program manager requests two
to four years; the energy-policy role requires subject knowledge without a year
count. The board did not demonstrate a large pool of near-entry-level jobs.

The [manual sample](review-sample.json) contains 27 actual regional records from
multiple sectors: 12 plausible, six stretch, six ineligible and three unknown.
It is deliberately curated, not a representative random evaluation. These
counts must **not** be used as precision estimates or source conversion rates.
“Plausible” means merits individual review under a 0–3-year band; it does not
assert the candidate has the experience, subject expertise or mission alignment.
The user's usual 0–2-year target is narrower. No employer-side active status was
independently verified, and no unfamiliarity to the user is assumed.

## Access and implementation

| New source | Public access used | Access/coverage limits |
|---|---|---|
| [Global Jobs](https://globaljobs.org/) | Advertised [RSS](https://globaljobs.org/jobs/feed.rss) plus visible index cards; public same-site job pages | No key or account. Index supplies employer, location, level hints and work arrangement; RSS supplies absolute posted dates and stable IDs. All 209 observed identities are preserved. [Terms/privacy](https://globaljobs.org/jobs/terms) and robots were checked. |
| [On Think Tanks](https://onthinktanks.org/jobsboard/) | Rendered job cards and public job descriptions | No key/account. Public locations, organizations and deadlines retained; description extraction omits unrelated recommendations/navigation. Only the rendered batch is covered, not an undocumented search API. [Privacy/site information](https://onthinktanks.org/privacy-policy/). |
| [Work for Nonprofits](https://workfornonprofits.org/jobs/district-of-columbia) | Public regional cards and published JobPosting metadata on job pages | No key/account. `/api/`, account routes and robots-disallowed filtered URLs are not used. Category, type, raw relative dates and published schema fields retained. [Terms](https://workfornonprofits.org/terms). Some exported descriptions are incomplete or have inconsistent contract metadata. |

Existing Senate and Union Jobs access remains functional. The shared reader
checks robots, paces pages, caps response size, restricts public HTTPS hosts/job
paths, validates redirects and stops on 403/429 without retry or bypass. Index
records survive detail failures and page caps. Requirements fields are not
invented when absent. Truncation is flagged on new specialty descriptions.

The new inspection plan has one freshest regional sentinel, then bounded strata
for plausible early-career evidence, employer/function variety, title variety
and seeded weak-signal review. Provider level metadata is a soft retrieval signal,
not a qualification check. Title matching uses word boundaries: “international”
is no longer mistaken for “intern.” “Senior Office Administrator” remains
eligible for inspection; “manager,” “director” and unfamiliar titles are not
hard exclusion gates. At the default cap, a small low-signal random stratum
remains. It can yield irrelevant jobs and does not guarantee every good role is
inspected. All uninspected records and inspection reasons remain in the audit.

The requested geography correction recognizes Camp Springs, Forestville, Upper
Marlboro and other explicit close-in suburbs with the correct state, and dotted
“Washington, D.C.” spelling. Maryland/Virginia alone are separately labeled
`regional_worksite_unknown`, routed to review rather than promoted as DC.
Arlington TX, Washington state and distant VA/MD cities do not become local.

Candidate-fit scoring formula/weights, shortlist selection and seniority/
specialist holds are unchanged. Geography corrections can affect the existing
geographic bonus/routing; that is the requested location fix, not a fit-model
redesign. Current ranking false positives/negatives therefore remain.

## Reproduce and verify

```sh
python -m pip install -r requirements.txt
python -m tools.audit_dc_sources --max-details 20 --inspection-seed 5 --output output/dc-source-audit.json
```

The audit defaults to all five boards. `--sources` can explicitly limit them;
`--inspection-strategy prefix` is available for controlled comparison. Live
indexes change, so a fresh capture is not expected to reproduce these counts.
The default exploratory seed is 0; this measured run explicitly uses 5.

Offline reproduction of the committed measurement makes no network requests:

```sh
python -m tools.compare_inspection docs/issue5/records.json --budget 20 --seed 5 --output docs/issue5/inspection-comparison.json
python -m unittest discover -s tests -v
```

All **77 tests** pass locally, including all 57 prior tests unchanged. New tests
cover the real minimized HTML/RSS fixtures, absolute/relative dates, metadata,
feed/index identity, public description parsing, failure/access modes,
inspection budgets and repeatability, exploratory sampling, ambiguous geography,
all-source audit integration and exact comparison reproduction. Scoring and
shortlist function ASTs were compared against the baseline. CI uses Python 3.11;
local execution used Python 3.13.2. The pre-existing pipeline test still emits
its Python 3.13 unclosed-file warning and passes.

The manual GitHub `dc` workflow now runs the five-source unranked audit, with an
optional inspection seed. The main engine supports `--sources dc-broad` or
individual new source names. Its legacy `dc` alias remains Senate+Union Jobs
for existing callers. No new credentials are needed. Existing optional
Muse/USAJOBS/ReliefWeb credential requirements are unchanged and were not activated.
No paid API, schedule, subscription, application or account signup was activated.

## Remaining gaps

Entry-level consulting, lobbying/public affairs firms, trade associations,
House offices, philanthropy and the full think-tank universe remain incomplete.
The added boards cover their participating employers, not the whole DC market.
Public Affairs Council and Daybook remain manual-only because of their access
terms; no bypass was added. ASAE/Philanthropy Jobs challenges were not bypassed.
Work for Good's current public app returned a client-rendered shell in the test,
so no private API was reverse-engineered. Impact Opportunity was investigated as
a possible later source, not integrated or counted here.

No claim of comprehensive coverage, representative fit precision, confirmed
active vacancies, complete qualifications, employer novelty to the user or
automatic application readiness is made. More permitted sources and independent
employer-side checks are needed before calibrating a candidate-fit model.
