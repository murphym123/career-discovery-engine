# Issue 25: employer-first discovery pilot

**Implemented the bounded path, but this development batch did not establish a job-retrieval gain.** It resolved 16 official-site claims and preserved useful unconventional hiring evidence. It found no supported ATS source among these 30 employers; collection is explicitly `not_collected`, not “zero vacancies.” The existing global mode remains available and produced more regional jobs in this observational comparison.

## Frozen external frame

[Open Data DC / DSLBD Certified Business Enterprises](https://hub.arcgis.com/datasets/dc299bc701f84f12b927eee9b3ec5a57_33/about), CC BY 4.0, was acquired end-to-end through its documented public ArcGIS query endpoint on November 6, 2026. Two paginated, field-projected responses yielded **1,994 currently date-eligible entities**. Contact, owner, email, phone, address and coordinate fields were not requested. Original response hashes were verified on re-reading. Five malformed WEBSITE values contained contact addresses: the importer excludes those values while retaining all organizations, and stored source copies redact them. None was in the selected batch; membership and observations are unchanged.

The 30 smallest SHA-256 values of `issue25-development-v1|frame_id` selected the batch **before** website inspection. Missing or malformed website claims remained in the sample. No industry, title, website availability or ATS filter was used. There is no exact normalized-name overlap with Claude’s 40-employer pilot; that is not independent/blinded validation. CBE headquarters evidence undercovers nonprofits, agencies and non-certified employers and never verifies job worksites.

[The frozen batch](frame-batch.json), [source/request evidence and outcomes](evidence.json), [review queue](review-queue.json) and [reproducible scorecard](comparison.json) are committed development evidence, not a production employer allowlist. The complete frozen frame and full unranked comparison records remain in the task archive.

## Measured comparison

Both arms started from the actual 2,368-board registry in [run 42193251891](https://github.com/murphym123/career-discovery-engine/actions/runs/42193251891), using separate copies. Both had ceilings of **180 requests, 64 MB and 600 seconds**, and 15 supported-board slots. Employer-first allocated 120 requests/32 MB/300 seconds to discovery and 60/32 MB/300 seconds to collection, at most three public pages per employer and two details per board. Global used the unchanged legacy mode: three seed queries, three details per board plus two shared inspections. Different allocation and live windows make this **observational, not a causal or market-recall comparison**. Five-source collectors were not rerun; the comparison base was explicitly empty.

| Observed unit | Employer-first | Existing global board-first |
|---|---:|---:|
| Eligible external-frame entities | 30 | Not a mode output |
| Official sites corroborated / careers endpoints retrieved | 16 / 4 | Not a mode output |
| Supported inventory groups actually attempted | 0 | 15; 2 failed |
| Regional posting identities / original descriptions | 0 / 0 | 24 / 19 |
| First observed regional identities / described identities in saved history | 0 / 0 | 17 / 12 |
| Distinct regional board groups | 0 | 7, not verified legal employers |
| Non-adapter channel leads / associated frame entities | 10 / 6 | Not measured for this frame |
| Identity/access review cases at employer level | 25 | Not measured for this frame |
| All unresolved employers, including unknown unconventional inventories | 30 | Not measured for this frame |
| Requests / observed response bytes | 67 / 5,922,461 | 52 / 9,650,481 |

Employer acquisition: **21:42:25–21:44:18 UTC**; exact windows are recorded in the evidence JSON. The first diagnostic pass used complete-response rejection at the same page cap and corroborated 11 sites. The final pass used explicitly marked bounded prefixes and generic CamelCase/logo-name corroboration, reaching 16. Original diagnostic and final observations are retained separately. Two final reporting labels were normalized offline: a `.js` reference is platform evidence rather than an inventory endpoint, and unconventional inventory unknowns enter the review queue. The original observation hash and the no-network postprocessing step are recorded; no new jobs or links were manufactured.

Examples of useful *new frame evidence*: BonstraHaresign’s observed careers page and functional `employment@bonstra.com` application route; Williams Adley’s careers page with an **ADP script reference**, not a verified job inventory; Softmax’s observed **Conrep** jobs URL; employer HTML careers routes at H.E.P. Construction, Micon and Coretec. These lead classes are preserved without another scraper. No secondary-board link appeared in this batch; synthetic tests cover retaining such links.

A few **global-arm examples**, not employer-first gains or candidate-fit judgments:

- [NNU National Organizer](https://job-boards.greenhouse.io/nationalnursesunited/jobs/4432987009): prior labor experience is optional; plausible early-career investigation, with travel, vehicle and possible relocation conditions.
- [Picnic Kitchen Ambassador](https://job-boards.greenhouse.io/cssmerge/jobs/8457880002): food-service/logistics experience is optional; part-time operational work with physical requirements. The board’s company label is not legal-employer proof.
- [HVAC/R Technician](https://job-boards.greenhouse.io/cssmerge/jobs/8909754002): specialist; at least five years of commercial HVAC/R experience requested.
- Bowhead NEPS Training Instructor: regional metadata retained, description not inspected within the cap; requirements and paid/current-vacancy status remain uncertain. No unpaid opportunity was positively established in these regional observations.

## Behavior and limits

The frame legal-name claim (a DSLBD business label, not independent legal-entity verification), operating label, parent/affiliate uncertainty, headquarters, board group and job location remain separate fields. Exact observed URLs determine supported-board handoff; no token or domain is synthesized from an employer name. Corroborated supported links use the existing registry, adapters and unranked formats. Shared boards are polled once; parent/affiliate legal-employer attribution is not inferred. Known-but-never-polled and previously-polled boards are distinguished from entities not linked in the supplied registry. The actual starting snapshot confirms the Issue #25 correction: Democracy Defenders Action and Giffords were known/unpolled, while the ANSER board was absent. This batch yielded no supported-board handoff, so that path is verified with synthetic regression cases, not claimed as a live success here.

Unknowns, denied/challenged pages, sparse JavaScript shells, identity conflicts, PDFs, email and unsupported/secondary channels remain evidence with next actions. Optional Work/Claude research is confined to the exported unknown cases; proposed URLs remain untrusted until deterministic verification. There is no model integration or automatic submission. Requests enforce observed-URL/DNS restrictions, robots, redirect checks, saved denial cooldowns, per-host pacing and stage budgets. Site bodies/contacts are not stored; page hashes and safe evidence fields are. Registry state commits atomically after outputs finish and preserves prior channel leads.

**280 tests pass**, including 28 new tests; `git diff --check` passes. Ranking, sealed Issue #9 materials, five-source collectors, ATS adapters and existing workflow modes are unchanged. No House bulletin parser, schedule, paid service, durable cloud store, application or merge was added. This is a reusable pilot and a negative result for ATS yield from one small-business frame, not evidence of strong market-wide recall.
