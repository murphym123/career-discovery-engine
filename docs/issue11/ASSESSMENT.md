# Issue #11: public ATS discovery pilot

This adds automatic discovery of previously unconfigured employer boards, then reads their current public Greenhouse, Lever and Ashby posting APIs. No employer names or tenant guesses are compiled into the discovery code. It is a limited pilot, not comprehensive coverage of the Washington employment market. Raw listing volume substantially overstates the useful gain.

## How unfamiliar employers are discovered

The registry starts empty. Public ATS URLs are extracted from the five existing sources' retrieved evidence and from bounded, free Common Crawl URL-index samples. Queries rotate across all three platforms, unrestricted host wildcards, generic organization prefixes (research, public, foundation, consulting, associations, institutes, policy and others), and the alphabet. The older Greenhouse hostname rotates in too. These prefixes are employer-name/URL prefixes, **not** full-text searches of job descriptions or a DC employer directory. Unrestricted exploration is necessary because organization names often reveal neither function nor location.

An available Internet Archive CDX URL index is a fallback for unavailable or empty board samples, not for access denials. Historical URLs establish only where to check: every claimed posting must come from a new, successful public provider response. Archived descriptions are never used as vacancy evidence. Public source links remain useful when indexing is incomplete. Optional evidence imports require observed ATS URLs plus a public provenance URL; a company-name list is not an input.

Each discovered tenant retains its observed URL, index pattern/page, archive timestamp and source. Provider-balanced manual-run selection reserves about 70% of slots for never-polled boards and 30% for refreshes, filling unused slots from either group. Failed boards cool down for seven manual runs. The registry is capped at 5,000 tenants and 20 evidence items per tenant; saturation stops adding new tenants until a registry restart, a pilot scaling limit rather than a complete market corpus. The saved registry, query offsets and index pages advance on subsequent **manual** runs. This is scalable discovery infrastructure with bounded sampling, not a one-time curated employer monitor.

## Access and platform findings

| Platform | Implemented access | Important limitations |
|---|---|---|
| Greenhouse | [Documented public Job Board GET API](https://docs.greenhouse.io/job-board.html); metadata first, bounded individual descriptions | No global company directory. Tenant IDs need independent URL evidence. Custom domains, uncrawled/new boards and stale URL indexes can be missed. `internal_job_id=null` signals prospect pools. |
| Lever / Lever EU | [Public Postings API](https://github.com/lever/postings-api); offset pagination, complete published description fields | No global directory. Initial Common Crawl samples yielded robots URLs, not usable boards; CDX found tenant URLs. Some archived tenants now return 404. No incremental DC result in the first two cohorts; the organization-prefix continuation yields one senior Alexandria engineering role, not an early-career gain. Adapter support is not itself a useful coverage gain. |
| Ashby | [Public job posting API](https://developers.ashbyhq.com/docs/public-job-posting-api), primary and secondary locations | Omit `isListed=false` direct-link-only records. `isRemote` alone does not establish remote work; use workplace type. Strong technology-employer bias in indexed samples. |
| SmartRecruiters | [Posting API investigated](https://developers.smartrecruiters.com/docs/posting-api) | Key/OAuth requirements and intended integration scope need resolution before a general no-key adapter. No credentials requested or private endpoints used. |
| Workday, iCIMS, BambooHR, ADP, Paycom, Oracle, Rippling and others | Observed platform links logged for later investigation | No private, authenticated, reverse-engineered or application endpoints integrated. These are material missing portions of DC hiring. |

The free [Common Crawl index](https://index.commoncrawl.org/) and [CDX API](https://github.com/webrecorder/pywb/wiki/CDX-Server-API) are bounded metadata sources. [Internet Archive CDX](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) is an independent availability fallback. Neither is a live or complete jobs index. Common Crawl page-count metadata permits a hash-spread starting page, avoiding always taking the alphabetical beginning. Archive page-count responses were incomplete in this pilot, so its effective initial pages remain alphabetically biased. The final implementation advances unknown-count pages on later runs and resets on a terminal 400/404 instead of assuming that page zero is the only page; this continuation is covered by an offline regression test, not a claimed live coverage gain. Collection changes, 404/5xx responses, repeated postings and incomplete indexing are visible in provenance.

## Measurements

See [metrics.json](metrics.json), [all regional record metadata](regional-records.json), [request and board provenance](request-provenance.json), and the [requirements review](REVIEW_SAMPLE.md). Request metadata is complete for the measured cohort runs; committed board provenance condenses repeated same-index/page URLs to representative examples. Full response bodies and original unranked audits are retained locally; full job descriptions are not republished in this PR. These are **actual retrieval evidence from 2026-11-05**, distinct from synthetic safety fixtures. Counts compare against the same new five-source capture: **329 index records, 95 inspected descriptions, 109 requests**, with all five sources reporting no adapter errors. Senate supplied 25, Union Jobs 58, Global Jobs 209, On Think Tanks 15, and Work for Nonprofits 22 records. Natural feed changes since earlier issues are not attributed to this change.

| Actual cohort | Boards selected / yielding rows | ATS rows, all locations | DC/DMV labels | Conservative unmatched regional rows / employer-title groups | Unmatched regional rows with descriptions | Reads, including seeding and shared details |
|---|---:|---:|---:|---:|---:|---:|
| Initial 60-board pilot (before page-spread correction) | 60 / 55 | 2,950 | 41 | 39 / 35 | 39 | 192 |
| Default 30-board page-spread pilot | 30 / 22 | 998 | 27 | 26 / 26 | 22 | 117 |
| Organization-prefix continuation (60 cap, only 28 discovered) | 28 / 16 | 244 | 12 | 11 / 11 | 11 | 108 |

The pooled **union**, deduplicated by provider/tenant/posting ID across the three overlapping cohorts, contains **4,151 ATS identities**, of which **76 have DC/DMV labels**. One probable existing-source crosspost and one obvious local talent pool are excluded, leaving **74 unmatched regional identities / 70 employer-title groups**. Of these, **70 have retrieved descriptions and four remain uninspected**. **2,701 nonlocal**, **463 US-remote** and **911 ambiguous-geography** identities are reported separately; they are not added to DC coverage. Eleven unmatched regional descriptions trigger a weak early-career range/text signal, including clinical roles; **this is not eleven suitable early-career jobs**. The requirements review identifies two plausible nontechnical early-career routes and one sales-adjacent stretch, not a validated shortlist.

The organization-prefix continuation starts at query offset six (research/public/foundation/American/consulting/association), with no employer names, and finds 27 index-derived boards plus one source-linked board. It selects all 28, producing 12 DC-labeled records, including one probable crosspost and one local record already seen in earlier cohorts. It contributes **10 additional unmatched DC identities beyond the other two pilots**. American Institutes for Research and American Flood Coalition demonstrate broader research, policy and communications source content, but their retrieved openings are largely senior or specialized. Lever contributes one real Alexandria engineering role, requiring extensive experience, rather than a promising early-career result.


“Not matched” excludes exact source identities, unresolved possible crossposts, supplied-date expirations and obvious talent/prospect pools. It does **not** establish suitability, employer acceptance of applications, distinct market-wide vacancies or employment-market recall. Same-employer/title groups conservatively collapse repeated titles for reporting, while every distinct requisition stays in the full audit. An outbound apply link alone can point to a related or stale role and is not automatically declared an exact duplicate. The NNU Regulatory/Policy Specialist source/ATS pair is excluded from the conservative gain as a probable crosspost requiring review.

The [review sample](REVIEW_SAMPLE.md) separates plausible early-career routes, adjacent stretches, specialist and senior controls, pools and missing evidence. The simple automatic early-career signal is deliberately weak: short year ranges can refer to clinical training or another context, single-year minimums and months can be missed, and titles are not qualification evidence. These signals are not a measured count of suitable early-career jobs.

The first pilot exposed alphabetical sampling bias; it is reported separately from the page-spread default-budget cohort. Its selected boards were chosen before the page-spread correction. Both cohorts subsequently received one real, bounded 20-description shared inspection pass. They are not a controlled before/after experiment or an untouched ranking validation. Shared inspection reuses the existing diverse retrieval strata, including weak/atypical titles, after all selected boards have had their metadata retrieved. Candidate-fit scores do not select these descriptions. No core ranking weights or rules changed.

## What genuinely improved and what is still missing

The five-source capture alone cannot reveal all roles on unfamiliar employer boards. Observed URL evidence now opens additional live employer inventories without guessing tenant names. The initial cohort found an ACLU investigator and a Cognition workplace coordinator that were not matched in the baseline; a federal sales-development role illustrates why a senior-sounding title must remain inspectable. Policy/advocacy, administrative operations and adjacent commercial work are present, but a default 30-board cohort was dominated by cleared contracting and technical roles. An engineering, retail or clinical job is not counted as a promising policy-career addition merely because it is local.

This does not yet demonstrate strong coverage of lobbying, associations, general consulting, think tanks, congressional offices, development or executive support. Existing specialist boards remain important, including the Senate source; House vacancies, small organizations with email/PDF applications, employer custom sites, unindexed ATS tenants, state-only worksite ambiguity and non-supported platforms remain gaps. No claim of comprehensive DC/DMV discovery or independently validated ranking is warranted. Provider concentration and low useful yield remain reasons to broaden lawful URL discovery and platform support, not to inflate the shortlist or change ranking weights.

## Run manually, credentials and safeguards

No new credentials, registrations, model calls or paid search services are required. In **Actions → DC career discovery → Run workflow**, keep `source_mode=dc` and `allow_paid_search=false`, enable `expanded_ats`, and choose `ats_board_budget` (default 30, range 1–100). `source_pages` also caps logical ATS seed queries (default 6, range 1–20 in Actions). `dc_detail_budget` also sets the shared post-board ATS inspection budget (default 20, maximum 30). Each Greenhouse board may first inspect up to three geographically prioritized descriptions. `experimental_ranking` is independently opt-in and remains explicitly unvalidated.

```sh
python -m tools.audit_dc_sources --max-details 20 --output output/dc-source-audit.json
python -m tools.expand_ats --base output/dc-source-audit.json \
  --registry data/ats-registry.json --board-budget 30 --seed-queries 6 --extra-details 20
# Optional, offline, explicitly experimental:
python -m tools.publish_experimental --input output/dc-expanded-audit.json \
  --output-dir output/experimental-ranking
```

The report artifact preserves **all original** `dc-source-audit.json/.csv/.md` files. Added `ats-discovery/ats-source-audit.json/.csv/.md` contains source provenance, failures, duplicate evidence and regional review rows. `dc-expanded-audit.json/.csv/.md` retains **every original and ATS sighting**, including duplicates, unknowns and nonlocal rows. Optional ranking consumes the combined JSON if available, otherwise the original. Missing or failed sources remain explicit; originals still upload if expansion fails. Pending independent-review records are matched by opaque committed hashes and withheld from automated fit evaluation, with unscored facts retained; no review packet is opened or reused as a test fixture.

A separate 90-day `public-ats-registry` artifact persists board evidence, rotation cursors and host cooldowns. It is restored from the last 15 manual workflow runs; expiration or unavailable artifacts can restart discovery and repeat previously seen boards. A blank registry is fully supported. There is no market-wide historical deduplication guarantee.

Only documented public GET endpoints are allowed. API requests use an identifying user agent, one-second pacing, no automatic retries, 20-second request timeouts, same-host safe HTTPS redirects, maximum 64 MB and 600 seconds per expansion, and at most `min(300, boards*6 + seed_queries*4 + 10)` requests. Optional public board-name pages obey robots rules and are skipped on stricter crawl delays. 403/429 stop that host and persist a cooldown; unavailable hosts are not bypassed. Greenhouse/Ashby have 5,000-row per-board normalization limits; Lever defaults to three 100-row pages. A 20,000-row stop-after-board threshold may include the final board (up to 24,999 rows), flagged in the audit. Descriptions are capped at 30,000 characters with truncation and original normalized-text hashes. Time/request/body limits and incomplete results are reported.

The existing paid modes remain guarded and unused here. No workflow was dispatched, no schedule enabled, no application submitted, and no merge performed for this assignment.

## Executable verification

```sh
python -m unittest discover -s tests -q
python -m compileall -q career_engine tools tests
git diff --check
```

155 tests pass, including the existing 124. New tests cover arbitrary unconfigured employer discovery and provenance, three provider normalizers, pagination and partial retention, unsupported/private URL rejection, redirects, robots, byte/time/request budgets, rate-denial cooldowns, duplicate ambiguity, actual minimized provider records, diverse shared inspection, manual workflow input limits and combined-audit selection, original-byte preservation, and independent-review quarantine. Synthetic tests never represent vacancies. The three small real-response fixtures retain factual metadata and just 20 description words each, with original response and fixture hashes. Full descriptions from the pending independent-review sample are not development/test data. The core ranker hash is unchanged. An [offline publication smoke check](workflow-smoke.json) on the actual default cohort retained all 1,327 combined rows, preserved the unranked input bytes, and made zero extra network/model calls. Retrieval failures remain explicitly marked as partial; this check does not measure ranking quality.
