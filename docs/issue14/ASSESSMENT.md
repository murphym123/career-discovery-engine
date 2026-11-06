# Issue #14: publication discovery and initial coverage audit

## What changed

The engine can discover unfamiliar hosted job inventories through an advertised
provider publication mechanism, rather than depending solely on previously
encountered employer links or archive URL ordering. Public JazzHR sitemaps supply
URL candidates; hosted career indexes supply actual metadata; public JobPosting
JSON-LD or the visible description section supplies bounded job text. Both old
and new JazzHR templates are supported. Case-sensitive posting identifiers are
preserved. Application forms, private state and keyed employer APIs are unused.

An Impact Opportunity RSS adapter adds a nonprofit publication channel. Generic
observation of RSS/Atom, sitemap, career-site and ATS links discovers further
channels in already-read public documents, without extra requests. Common Crawl,
source-evidence links, Greenhouse, Lever, Ashby and all five original collectors
remain. No employer allowlist, guessed tenant names, paid search, schedule or
application submission was added.

An independent coverage ledger records source identity, discovery evidence,
retrieval state, inventory scope, metadata retained, description availability and
unknown totals. It distinguishes unresolved publication links from inspected
inventories. Dated DC access investigations are explicitly historical and not
misrepresented as fresh checks on every run. Saved manual-run registry artifacts
retain cursors, recent source states and seen posting hashes.

## Actual comparison

Data were acquired November 5, 2026 in America/Denver, from
**2026-11-06 00:28:07 through 03:30:56 UTC**, not in an instantaneous snapshot.
The full unranked audits and frozen response corpus are retained with the task's
local evidence artifact. The committed [metrics](metrics.json),
[105 local-labelled metadata records](actual-dc-records.jsonl),
[retrieval hashes](retrieval-provenance.json), and
[inspected-source ledger excerpt](coverage-ledger-excerpt.json) are reviewable.
These are actual retrieved records, not invented fixture vacancies.

The five-source baseline returned **303 records**: Union Jobs 58, Global Jobs
209, On Think Tanks 15 and Work for Nonprofits 21. **Senate failed with HTTP 404**
and returned zero records. The baseline used 90 requests and observed 75
descriptions. This is a partial five-source capture, not evidence of no Senate
vacancies. Both comparison arms use these exact same 303 records. Their original
bytes are SHA-256 recorded; the original records remain the identical prefix of
the combined audit.

| Bounded ATS comparison | Previous provider-spread selection | Regional publication-evidence selection |
|---|---:|---:|
| Board polling cap / boards attempted | 30 / 30 | 30 / 30 |
| Boards returning records | 21 | 27 |
| Logical retrieval requests used / cap | 84 / 180 | 100 / 180 |
| Raw ATS identities, all geographies | 449 | 1,316 |
| DC/DMV-labelled identities | 8 | 105 |
| Board groups with local labels | 4 | 15 |
| Local unmatched records after known pool/volunteer/expiry exclusions | 4 | 95 |
| Above unmatched records with descriptions | 4 | 24 |
| All descriptions, including nonlocal | 292 | 260 |

**101 of the new arm's local identities were absent from the old arm.** This is
a within-test set difference, not 101 verified additional jobs. Of the 105 local
records, three are probable baseline cross-posts, four inspected descriptions
explicitly describe unpaid appointments, and three describe registration/future
opening pools. The remaining 95 are unmatched candidates, **not 95 established
employment opportunities**: 71 lack descriptions, 38 further MOTA appointment
records remain uninspected, and many other records are specialist work. Do not
extrapolate the four inspected unpaid appointments into a verified classification
of all uninspected records. Cross-post detection is conservative flagging, not
perfect deduplication or a legal-employer census.

The substantive recoveries include a law/policy institute's program associate,
paid humanitarian fundraising outreach, membership administration and quantitative
clean-energy analysis. [Examples and qualification evidence](RECOVERED_EXAMPLES.md)
show their differences and limits. This demonstrates some recovery beyond the
prior technology/intelligence concentration, but not comprehensive policy,
association, consulting or government-affairs coverage. Title-based function and
early-career signals remain diagnostic metadata; they are not suitability labels.

### Method and limits of the comparator

The controlled tool replays hash-verified actual public responses with **zero
network/model calls and no live fallback**. Both arms receive the same expanded
candidate pool and feed capture, same five-source baseline, 30-board cap,
180 logical-request cap and 20 shared extra-description cap. The legacy strategy
uses its existing provider spread and three initial description slots per board;
the regional strategy uses source/geography evidence and one initial slot, then
allocates the shared description budget. Thus this compares the combined
selection/allocation change, not an isolated causal effect of geography alone.
It does not recreate the original GitHub run's 1,045/41 result or measure every
request cost of a fresh end-to-end search. Shared discovery acquisition happened
before replay and is reported separately below.

Regional selection checked 12 evidence-routed boards, six completely non-targeted
boards, seven weak function-exploration boards and five broad-fill boards. Weak
occupational URL hints are capped; they are not verified job locations or a
candidate fit filter. At least 20% non-targeted exploration remains independent
of those hints. Source ordering and incomplete archives still bias the candidate
pool. Developmental captures informed parser/routing corrections, so these are
engineering diagnostics, not an untouched benchmark or independent validation.

The capture manifest contains 197 usable URL/parameter responses. It selects the
latest preserved matching body for shared URLs; it is not a complete wire log of
every developmental request. Earlier response MIME headers were reconstructed
from already-accepted body type where the original logging omitted them. Bounded
or failed bodies remain incomplete, never fabricated into full inventories.

## Discovery mechanisms and source completeness

Two advertised JazzHR sitemap prefixes were read up to 1.5 MB each. They contained
8,180 and 8,186 complete URL entries, respectively. Those are **URL seeds**, not
vacancies, and their denominator beyond the prefix is unknown. Rotation across
the five advertised sitemap paths is persisted. Provider sitemap last-modified
timestamps are not treated as job posting dates or proof of current vacancies.

The shared registry contained **1,604 board URL candidates** after source edges
and archive seeds. The final regional ledger has **1,634 endpoint entries**:
1,626 source candidates plus eight unresolved publication links. Its states are
six searched, 30 partially retrieved, four inaccessible and 1,594 not yet covered.
These totals include original sources and dated gap investigations as well as
ATS candidates; they are not a count of employers in DC. The excerpt commits
inspected inventories/access observations, not the entire 1,604-board registry.

| Inventory examined | What can be reconciled | Remaining boundary |
|---|---|---|
| Greenhouse NNU | 14 retained / 14 listed endpoint items, provider total 14; three descriptions | All employment at NNU outside that public board is unknown. |
| Other completed GH responses | Seven retained / seven items for one board; a second returned zero / zero | Endpoint completion is not market coverage or a reason to discard empty sources. |
| Lever | Ten retained / ten items; terminal short page establishes observed pagination completion | No claim about unpublished or other-platform work. |
| Ashby | 134 / 134 and 78 / 78 listed items; descriptions included | Mostly not DC. Hidden/unlisted jobs are not collected. |
| JazzHR hosted HTML | Every parsed visible row retained, subject to 1,000-row cap; source-scoped denominator recorded | No independently declared full inventory total or undisplayed-pagination guarantee. Two inspected HTML schemas returned no parsed rows and remain unresolved, not zero-vacancy findings. |
| Impact RSS | All 50 returned items parsed; four local labels | Feed-window completion only. One local associate cross-posts the baseline; other local items do not establish new early-career gains. Bodies can omit required qualifications. |
| Original five sources | 303 observed records retained; 75 descriptions | Feeds/indexes can overlap or truncate; no defensible source-wide total inferred. Senate 404 is access failure. |

The optional DC-HQ Wikidata directory pilot found 197 distinct website leads and
inspected eight sites, recovering **zero supported ATS boards**. It stays off by
default; HQ is not a worksite. The global publication mechanism and archive edges
do not depend on this DC-specific acceleration.

Identical captured ATS records replayed against the next registry run produced
zero first-observed and 1,316 previously observed identities. This verifies saved
identity behavior only; **it is not a second live search or evidence of future
incremental yield**. Source status history is separately bounded to three recent
snapshots; archive cursors and candidate inventories remain finite/budgeted.

## Portable cold starts and gaps

Two actual Tampa, FL starts used empty registries, no DC specialty sources and no
seed employers, with four-board/50-request caps, one sitemap prefix and no archive
queries. The first read 102 ATS records from four boards in 19 requests, with
**zero Tampa matches**. A fresh follow-up after adding weak city-only source-URL
routing read six requests and zero ATS records: its first selected very large
board exceeded the bounded response/byte limit, stopping further retrieval. The
RSS still returned 50 items. No limit bypass or unlimited retry was attempted.
These tests show portable execution and a failure boundary, **not successful
coverage of a second market**. A synthetic unknown-employer Tampa fixture verifies
the supported local-record path and is explicitly separate from these real data.

| Gap / publication ecosystem | Observed evidence and state |
|---|---|
| Congressional employment | Senate collector 404 in this capture. House's official employment page exposes an email channel and a JS careers site; inventory parsing is not implemented. No private token/internal API was used. |
| Associations / public affairs | ASAE CareerHQ returned a bot challenge (HTTP 200); PAC terms restrict automated collection. Neither restriction was bypassed. |
| Nonprofits / policy / advocacy | Impact adds an accessible window and Brennan adds a public ATS inventory; organizations outside indexed/advertised channels remain unknown. FoundationList terms restrict extraction. |
| PoliticalJobHunt | RSS was observed during investigation, but restrictive terms remain unresolved. No adapter, benchmark contribution or recurring retrieval enabled. |
| Consulting / contractors | Some public ATS inventories now exposed; many remain technical, cleared or senior. Workday, iCIMS, Paycom, ADP and other observed platforms lack supported inventory adapters here. |
| Universities, media, small offices, recruiters | Custom pages, PDFs, newsletters, email and login-only inventories remain unsearched or only partially visible. A recruiter feed creator is not automatically the legal employer. |

The [dated gap fixture](../../career_engine/market_fixtures/dc_publication_gaps.json)
keeps public evidence and unresolved states separate from the current collection.
Robots denial, rate limits, challenges, stricter crawl delays and unsafe redirects
stop collection; there is no bypass. New endpoints need policy review and a
documented/public inventory adapter before moving from discovered to searched.

## Reproduction, credentials and verification

No new credentials are required. All additions use public GETs with strict
endpoint/size/time/request budgets and pacing. In the manual workflow use `dc`,
enable `expanded_ats`, keep `allow_paid_search=false`; experimental ranking is a
separate default-off option. The workflow uploads all existing artifacts plus
coverage ledgers and preserves the registry for 90 days. No scheduled run was
activated and this change has not been merged.

```sh
python -m unittest discover -s tests -v
python -m tools.compare_discovery --base CAPTURE/baseline.json \
  --registry CAPTURE/registry.json --candidate-capture CAPTURE/candidate-capture.json \
  --responses CAPTURE/responses.json --output-dir output/paired
```

`CAPTURE` is the retained real response corpus, not a network-enabled fallback.
Synthetic fixtures exercise pagination, boundaries, denial, budget exhaustion,
normalization, duplicate flags, XML safety, title/identity checks and generic
discovery. A minimized actual Montréal/DC metadata conflict is committed with
its original response hash. Existing tests and the full audit remain intact.
All **197 executable tests pass**; compilation and whitespace checks pass. The
pre-existing unclosed CSV-file ResourceWarning in the pipeline test is unchanged.
An offline experimental-publisher smoke check uses the real combined collection
only to verify compatibility and preservation; no ranking-quality optimization
or validation claim is made. It retained all **1,669 combined rows**, preserved
the input hash, and made zero source/model calls. The ranker file hash is
unchanged. The independent review packet remains sealed.

There is no known denominator for undiscovered employers, inaccessible jobs,
unadvertised channels or the entire market. **No market-wide coverage percentage
is estimated.** This is a first source inventory audit and a measured discovery
increment, with material gaps remaining.

## Public primary references

- [JazzHR public careers pages](https://www.jazzhr.com/onboarding/careers-page) and [advertised sitemap paths](https://app.jazz.co/robots.txt).
- [Greenhouse public Job Board API](https://docs.greenhouse.io/job-board.html), [Lever Postings API](https://github.com/lever/postings-api), [Ashby public posting API](https://developers.ashbyhq.com/docs/public-job-posting-api).
- [Impact job board](https://impactopportunity.org/jobs/), [RSS](https://impactopportunity.org/rss/), [terms](https://impactopportunity.org/terms-of-use/).
- [Wikidata query service](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual) and [data access](https://www.wikidata.org/wiki/Wikidata:Data_access/en).
- [House employment channels](https://www.house.gov/employment/positions-with-members-and-committees), [ASAE CareerHQ](https://careerhq.asaecenter.org/jobs/), [PAC terms](https://pac.org/terms-of-use), [FoundationList terms](https://www.foundationlist.org/terms-conditions/), [PoliticalJobHunt terms](https://www.politicaljobhunt.com/terms-of-service).
