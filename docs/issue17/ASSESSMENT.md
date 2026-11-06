# Issue #17: production reliability and regional description retrieval

## Outcome and scope

The [new manual run #42193251891](https://github.com/murphym123/career-discovery-engine/actions/runs/42193251891)
completed successfully and uploaded the full audit. Its explicit status remains
**PARTIAL COLLECTION**: Senate is inaccessible and one ATS candidate returned
404. Work for Nonprofits returned 21 records. All 30 selected ATS boards were
attempted, across all four supported platform families. No ranking or AI
evaluation ran; no schedule, paid search or application submission was enabled.

The focused implementation fixes source handoff, platform sampling, per-response
failure isolation and regional description allocation. It does not establish
that the underlying Senate site is repaired or that the resulting jobs suit a
particular candidate. The [real-record examples](RETRIEVAL_REVIEW.md),
[metrics](metrics.json), [source-ledger excerpt](coverage-excerpt.json),
[local metadata](actual-local-records.jsonl), and [response hashes](response-provenance.json)
separate publication evidence from eligibility and market-wide coverage.

## Evidence from the exact failed production run

[Run #42159678800](https://github.com/murphym123/career-discovery-engine/actions/runs/42159678800)
used main commit `16eae519a217a3c3bb4953d44c1fcfb0b83f59e1`. We downloaded its
report and registry artifacts and verified GitHub's SHA-256 ZIP digests:

- Report: `8ed37994ec7ec4b5a390cac153ba1e7c3bd365d0bc18b1f0c1a0086a27c3e187`.
- Registry: `b000ca0dc573b179fb1b84ffc7ea83cec6f74bb5cfb044f7b0db52f8b09e5233`.

The original five-source step exited 1 but preserved 281 records: Union Jobs 58,
Global Jobs 208 and On Think Tanks 15. Senate returned 404. Work for Nonprofits
reported `robots_disallowed_or_unavailable`; its one robots request produced no
returned HTTP observation in the artifact. That supports a robots-retrieval
failure, but the old artifact cannot recover the exact exception/status. It
does **not** prove a permanent robots prohibition or zero vacancies.

ATS discovery selected 30 but attempted only 19 boards (18 JazzHR, one GH).
Sixteen JazzHR boards returned all 1,832 ATS identities. A large hosted index
raised `response_or_byte_limit`; the runner treated that ambiguous per-response
error as a global stop. The audit retained 113 local-labelled records, 31 with
description text; only 22 descriptions passed the older unmatched/non-pool
diagnostic. These are different denominators. MOTA, Maania, Dr. Demo, Butler and
Language Trainers supplied 91 of 113 local labels, with significant uninspected
and specialist material. The 2,163 combined sightings were not 2,163 verified
vacancies. The original coverage ledger had 2,380 heterogeneous endpoints, not
2,380 employers or a known universe of market sources.

## Specialty sources and access boundaries

The [official Senate vacancies page](https://employment.senate.gov/job-vacancies/)
advertises the careers home page. The existing regional index still returned
404 during this investigation. Its returned document contained normal site
navigation but no listing inventory. A robots-permitted check of the advertised
home page returned **403**, both locally and in the new cloud run. Collection
stopped; no category sweeps, disallowed search/feed endpoints, alternate agents,
browser bypass or private API were attempted.

The adapter now permits one fallback to that advertised public home page **only
after 404/410**. It never retries a denial or robots failure. A successful
fallback would preserve the original route error as partial retrieval, retain
every visible card and remain bounded to the rendered batch. The current live
result is still failure: 404 followed by 403, conspicuously reported red. This
is a bounded recovery path and honest diagnosis, not a claim that Senate access
has been restored.

Work for Nonprofits' currently published [robots rules](https://workfornonprofits.org/robots.txt)
allow the clean DC index and public job paths but prohibit API/authenticated
areas and several query-filter paths. Only permitted clean paths were used.
Local checks returned robots 200 and index 200; the new GitHub run also returned
200 and retained 21 index records with 20 descriptions. No new alternative
source or bypass was needed in this run. Recovery may be transient: it cannot
be causally attributed to increasing the robots timeout from eight to twelve
seconds. We do not silently retry failures.

Robots observations now distinguish explicit disallow, HTTP failure/redirect,
network exception class, invalid HTML/challenge and stricter crawl delay. They
are recorded per source along with index URL, outcome and fallback flag. Rules
remain fail-closed; responses are closed. An unexpected adapter exception also
preserves earlier/already-yielded rows and records its type, without logging
arbitrary response state or exception text.

### Exit and handoff policy

Standalone `audit_dc_sources` remains strict by default. The manual workflow
explicitly opts into `--allow-partial`: useful surviving source records permit
exit 0, while JSON/Markdown and the GitHub summary prominently retain partial
status, red failed sources, yellow partial sources and exact errors. If all
original sources fail, it still exits 1. Missing/invalid expected artifacts also
fail the handoff. A successful ATS stage cannot erase specialty-source errors.
Status describes bounded collection health, never complete publisher coverage.

## Balanced polling and bounded failure isolation

Exploration is stratified across available **platforms**, rather than uniform
over a registry dominated by JazzHR. At least 20% of slots remain non-targeted
exploration; source/local geography evidence still guides the other routes.
One platform normally gets at most half the board budget when alternatives
exist. Only exhaustion of other eligible platform pools relaxes that cap; no
fake board candidates are invented. Fresh boards remain preferred, and observed
failure cooldowns remain. Selected platforms are interleaved near the front so
an early global limit cannot leave every minority platform at the end.

The transport distinguishes `response_too_large` from global `request_budget`,
`byte_budget` and `time_budget`. A per-response limit closes that response,
retains prior data and continues to the next board if the global budget allows.
Actual global exhaustion still stops safely. Denials stop their host, not
unrelated providers. The report now distinguishes selected, attempted and
not-attempted boards, with explicit reasons; the coverage ledger leaves selected
but unattempted candidates pending. The historical large-board failure was on
cooldown for the fresh comparison; its exact old run is evidence of the defect,
while executable oversized-response and continuation tests verify the repair.

## Description allocation and the matched response test

Regional mode polls metadata from every selected board before spending shared
detail slots. For missing GH/JazzHR descriptions, it prioritizes regional labels
and unresolved regional worksites, spreads requests across boards, diversifies
functions and keeps seeded broad exploration. It uses no candidate profile or
fit score and does not reject atypical titles. All uninspected and nonlocal
records remain. A base budget of 20 permits up to 20 additional regional slots
(40 total); the absolute shared cap is 60. Unscoped remote backlogs do not trigger
conditional expansion, and global request/byte/time limits still apply.

We used the exact production run's saved **post-run registry** and 281-record
baseline as common inputs. The pre-fix regional strategy and fixed strategy
were acquired against a shared response cache and then replayed strictly offline,
without live fallback. This is a next-run diagnostic, not a reconstruction of
the failing run's already-consumed state. The shared RSS window is the captured
production feed, not a newly discovered gain. The fresh public response window
was **November 6, 2026, 09:08:39–09:12:13 UTC** (02:08–02:12 America/Denver).
There are 142 URL/parameter responses: 102 acquired for the prior arm and 40
additional responses for the fixed arm. Failed/bounded bodies remain incomplete
and hashes are verified; no complete body is invented.

| Same registry/baseline and 30-board/180-request caps | Pre-fix regional | Fixed regional |
|---|---:|---:|
| Platforms attempted, JazzHR / GH / Ashby / Lever | 26 / 3 / 1 / 0 | 15 / 5 / 5 / 5 |
| Boards attempted | 30 | 30 |
| Logical retrieval requests | 102 | 100 |
| Local-labelled metadata records | 135 | 134 |
| Local records with descriptions | 33 | 57 |
| Local metadata lacking descriptions | 102 | 77 |
| Description fraction of observed local records | 24.4% | 42.5% |
| Board groups with local records | 17 | 16 |
| Board groups with local descriptions | 16 | 16 |

For the **117 common local identities**, descriptions increased from 29 to 40:
19 became described, while eight lost a description slot. The eight remain
uninspected in the full audit, not deleted; they include student internships,
specialist roles and appointments. The fixed arm added 17 local identities from
other selected boards but did not retrieve 18 from the old arm's boards. Its
broader platform sample did **not** increase local employer-board count. This
measures the combined selection and allocation change, not an isolated causal
effect of doubling slots or a claim of more suitable entry-level jobs.

All 57 fixed-arm local descriptions were untruncated. That means the entire
returned public description section was retained within our limit; it does not
prove every mandatory qualification is present or that applications remain open.
Three inspected unpaid appointments and four pools/registration notices were
explicitly identified in this arm. Most of the 42 MOTA records remain uninspected;
they cannot be counted as verified paid jobs. Forty-five ATS records still need
geography review; source descriptions and titles can expose remote arrangements
that a regional index label alone does not resolve. Raw evidence is preserved.

## Genuine manual GitHub run after passing tests

Run #42193251891 executed branch code `068714c5bd418e2b1d3f4db1e77cd080151339e1`
after all 211 tests and GitHub CI passed. Inputs were DC mode, expanded ATS true,
30 boards, detail budget 20, six free discovery queries, paid search false and
experimental ranking false. It restored saved discovery state and collected
fresh specialty and ATS data. It completed in about six minutes, without merge.

- Original sources: 302 records; Senate still failed, Work for Nonprofits
  recovered 21. All original records and the original audit hash were preserved.
- ATS: all 30 attempted, 15 JazzHR and five each GH/Ashby/Lever; 29 boards
  returned records. One Lever URL returned 404 and remained an access failure.
- Regional metadata: 83 records; 57 untruncated descriptions, 26 still missing.
  The observed description fraction is **68.7% of these 83**, not of the market.
  The unmatched diagnostic has 55 described records; that excludes different
  duplicate/status conditions and must not be compared to total descriptions.
- Full collection: 3,288 sightings (302 specialty, 2,936 ATS, 50 RSS). Raw volume
  is not the success criterion. New source evidence and discovery cursor state
  changed the selected boards, including communications and nonprofit channels.
- Report: **PARTIAL COLLECTION** in [the exact saved handoff](live-collection-status.md),
  independently of the workflow's successful exit. Experimental ranking skipped.

The live ledger has 2,750 endpoint entries: 14 searched, 23 partial, five
inaccessible and 2,708 pending. They include URL candidates and dated access
observations; they are not a count of verified employers. Current inventory
attempts are distinguished from historical restrictions. Source-specific API
metadata totals, visible HTML denominators and description counts stay separate.
The earlier run and this later snapshot are **observational**, not a matched
before/after estimate. The matched replay above is the controlled comparison.

## Validation and remaining limits

All **211 tests pass**, including all 197 pre-existing tests and 14 new source,
access, partial-exit, provider-skew, oversized/global-limit, pending-ledger,
description-spread and denial-isolation regressions. Compilation and whitespace
checks pass. The pre-existing pipeline-test ResourceWarning is unchanged.
GitHub CI passed and the real manual workflow completed. No independent job-fit
validation was performed; the sealed review packet was not opened.

Full old/new/live unranked JSON/CSV, raw response snapshots and the evidence runner
are retained in the task's local evidence artifact. The committed provenance
contains hashes and public URLs, not full HTML/form state. To reproduce, use
the retained `qa_arm.py` with an archived pre-fix code tree and current code,
`--offline`, and the supplied snapshot/QA inputs. It rejects uncaptured requests;
no paid/live fallback is enabled. The original ranking file hash is unchanged:
`22eeaea408d245af83dc476ea734286f1ac773cbbb7a5b7f0d71d2e3e0fa6266`.

Senate remains inaccessible. Generic career links, unsupported ATS platforms,
small-office channels and sources with unknown totals still have material gaps.
The sample adds platform reach and qualification evidence, but many new records
are senior, technical, student-only, unpaid or geographically ambiguous. Neither
source success nor a nonempty description establishes suitability. No percentage
of all employment opportunities is estimated.
