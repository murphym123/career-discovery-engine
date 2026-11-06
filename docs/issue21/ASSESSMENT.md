# Issue #21 — public iCIMS discovery and DC geography

## Outcome

This increment adds a general public iCIMS HTML collector and routes discovered iCIMS links through the existing manual ATS expansion. It requires no credentials and contains **no production employer list**. Brookings and AEI remain development examples, not an allowlist or an independent validation set. The [Issue #19 coverage report](../coverage/DC_EMPLOYMENT_COVERAGE_INTELLIGENCE.md) is the evidence base; no market-wide coverage percentage is introduced.

A fresh-registry, equal-budget comparison increased regional-labelled ATS records with untruncated descriptions **from 6 to 12**, and regional employer-board groups **from two to three**. Seven regional iCIMS records were recovered from a board linked by an existing source listing; one previously described record lost its detail slot and no longer acquired a regional label. It remains in the full unranked audit. The operational gain is **modest and still concentrated in contracting/specialist work**. None of those seven was established as a straightforward generalist 0–2-year opportunity.

Separately, three other employer inventories exposed **83 metadata records and 14 regional-labelled records**. The existing shared description allocator retrieved untruncated public descriptions for all 14. Two inspected CFR records have plausible early-career requirements, with material skills and experience conditions. These direct tests demonstrate useful inventory content; **the broad discovery run did not independently discover all three boards**. Do not add their 14 records to the operational gain or call them independently validated recommendations.

## General collector and discovery behavior

The adapter accepts observed employer `*.icims.com` public career/search/job URLs, excluding administrative, candidate/application, API and social-distribution routes. It normalizes a board to its public search route and uses tenant + numeric posting ID for identity. Reposts with different IDs remain distinct identities, even if titles match. Public iframe/link edges, JobPosting URLs embedded in already-read documents, source snippets and previously unsupported registry links can all route into the adapter.

Discovery retains the existing broad sources. In regional mode, one of the existing free URL-index query slots is reserved for iCIMS when at least two slots are available. Historical URLs are leads, never current-job evidence. Existing geographic evidence guides board polling, while the platform-balanced exploratory allocation remains. The manual GitHub expansion also enables **three** existing employer-link inspections: a bounded public career-link graph seeded by source evidence and the open DC-HQ directory. A directory headquarters is not a job worksite. The CLI's optional employer budget remains zero unless requested. There is no dependency on a precompiled employer registry, search/model key, or Issue #19 sample file.

Only public GETs are used. Every iCIMS request requires established robots permission; invalid/unavailable robots fail closed. Delays up to ten seconds are respected on **each request and redirect**; stricter delays are skipped. Denials/challenges stop that host. Cross-host redirects, private paths, credential/session query parameters and application routes are rejected. Byte/request/time bounds and per-response failure isolation remain. No internal iCIMS API, tokens, form submissions or access bypass was added.

The index parser supports the observed card variants: header location labels, repeated `Location : Location` fields, and separate country/state/city fields. It follows **published next-page links**, ignores disabled pagination links, deduplicates IDs and records page numbers/totals. Shells or unrecognized empty pages report unresolved/partial, not successful zero collection. A verified empty requires a recognizable public no-results message. Contradictory or capped pagination does not establish completeness.

For a large inventory, the collector may prioritize an **actually offered public GET location-search option** rather than spend all pages on unrelated global records. Values come from the page's search form, not guessed location IDs. The initial rows are retained. Only one regional option is prioritized per bounded board attempt; saved cursors rotate among observed regional options on later manual runs. Unsearched options and incomplete global pagination remain explicit. Filter membership is discovery evidence: the publisher may return other locations, so it does not override the displayed worksite labels. Jhpiego's DC filter, for example, also returned Baltimore records; those were preserved without calling them DC jobs.

Metadata is collected before descriptions. iCIMS joins the existing shared, geographically guided description allocation, preserving employer/function diversity and uninspected records. Dedicated public description sections retain their headings and adjacent published conditions; candidate forms and unrelated navigation are excluded. Canonical/structured posting IDs protect against attaching another job's description. Index and detail location evidence are retained separately. Quarantined Issue #9 matches never request descriptions; the sealed packet was not opened. Ranking code is unchanged.

## Geographic repair and audit integration

`US-DC`, `US-DC-Washington`, and verified country/state/city equivalents now identify DC. Raw multi-location labels remain separate; remote/eligibility ambiguity remains visible. Negative tests cover Washington State, Washington County, ambiguous Washington, Arlington TX, Baltimore, unscoped remote and typos. A body mentioning DC does not override a nonlocal index worksite.

The current selection is explicitly named **`dc-nearby-dmv`** in market metadata. Strict District-only scope is named separately, and full Washington MSA parity is explicitly **not implemented**. The existing nearby-city fixture was not widened to the full MSA. This increment fixes aliases, not every locality gap identified in Issue #19.

The coverage ledger retains original sources, observations and historical restrictions. Supported iCIMS edges are promoted from unsupported links without losing provenance. Its current capability list includes iCIMS; historical ecosystem observations remain dated. ATS reports expose per-platform **newly discovered / discovered candidates / attempted boards / retained index records / described records**, alongside per-board pagination, completeness, source status and description denominators. Unknown, inaccessible and verified-empty inventories remain different outcomes. A partial iCIMS inventory keeps the overall collection visibly partial even without an HTTP error.

## Equal-budget fresh discovery comparison

The prior implementation is current main before this increment (`d887063f379b2f422390654a7af6e6e2601935e1`), locally archived from an equivalent implementation tree. Both arms start with an **empty registry**, no imported employer leads and the same 302-record five-source capture from [run #42193251891](https://github.com/murphym123/career-discovery-engine/actions/runs/42193251891). That original source capture is dated **09:14 UTC**, not freshly recollected here. This is fresh ATS/source discovery against a frozen original-source baseline, not a fresh run of every source or a new GitHub manual production run.

Actual public responses were acquired on **November 6, 19:59:29–20:04:07 UTC**. Shared responses were reused, then both arms were replayed strictly offline with hash verification, no HTTP/DNS fallback and a common observation cutoff. This avoids job-content drift for common URLs; changed seed queries and selected boards still mean that the comparison measures the **combined increment**, not an isolated causal effect of geography versus discovery versus allocation.

Both arms use 12 board slots, six free discovery queries, three employer-site inspections, no JazzHR sitemap pages, a 12-page base shared detail budget with up to 24 conditional pages, three index pages per board, and ceilings of 150 requests / 64 MB / 540 seconds. Actual requests differ because different public sources were selected; equal budgets do not mean equal consumption. The three employer-site checks produced no new boards in either arm. The live iCIMS URL-index query returned no new board leads in its bounded sample, with index timeouts and fallback observations retained.

| Captured ATS metric | Prior | New |
|---|---:|---:|
| Boards attempted | 12 | 12 |
| Total logical requests, including discovery/robots | 58 | 66 |
| Board retrieval/detail requests | 31 | 38 |
| Retained index identities, all locations | 282 | 324 |
| Regional-labelled identities | 6 | 12 |
| Regional untruncated descriptions | 6 | 12 |
| Regional employer-board groups | 2 | 3 |
| Regional records not matched to the five-source capture | 2 | 8 |
| Regional possible crossposts, retained for review | 4 | 4 |

The [actual discovery edge](operational-discovery-edge.json) is a Global Jobs listing linking the Bowhead public iCIMS inventory. It supplies employer/source and Arlington evidence; the engine did not guess a tenant name. Bowhead displaced one nonregional Ashby board within the same 12-slot budget. The new collector retained **56 Bowhead index identities**, seven regional labels and seven descriptions. Its chosen DC form window was exhausted, but the global inventory and other offered nearby filters were **not** exhausted. The global denominator remains unknown, not 56.

Seven regional identities were added; one R Street record remained retained but lost its description slot, so it did not acquire the regional label extracted from its detail in the prior arm. The net label/description gain is six. This illustrates allocation tradeoffs, not deletion. The full original baseline, original listings and all newly retrieved rows remain in the combined unranked artifacts.

The seven Bowhead identities include two same-employer/title senior maritime records with different posting IDs, a broad multi-role contract recruitment notice, technical/security conditions and a subject-matter-specific Outreach Coordinator. They cannot be represented as seven unique suitable vacancies. Four possible crossposts elsewhere in the capture remain flags, not automatic merges. No application-acceptance check or confirmation of current paid vacancies was performed.

## Other real inventories and actual qualifications

The [three-board diagnostic frame](independent-frame.json) was selected from external public searches, independently of the Brookings/AEI pair and before acquiring these inventories. It was purposeful, not representative or blinded. It includes a policy institution, an international-health organization and a contractor. The observations were then used to repair parser variants, so they are now **development/regression evidence**, not a reusable untouched holdout.

| Other employer inventory | Retained metadata | Regional labels | Regional untruncated descriptions | Completeness boundary |
|---|---:|---:|---:|---|
| [CFR](https://careers-cfr.icims.com/jobs/search) | 19 | 8 | 8 | Public Page 1 of 1 metadata window exhausted |
| [Jhpiego](https://jobs-jhpiego.icims.com/jobs/search) | 21 | 2 | 2 | Initial global batch plus offered DC filter; selected regional window exhausted, global inventory partial |
| [Culmen](https://careers-culmen.icims.com/jobs/search) | 43 | 4 | 4 | Public Page 1 of 1 metadata window exhausted |
| **Observed total** | **83** | **14** | **14** | Source-local windows, not market coverage |

The metadata-first/shared-detail run used **22 logical requests**, including robots and index reads, with a shared base detail budget of nine (conditional maximum 18); 14 descriptions were actually requested. All 83 rows remain, including off-market and uninspected records. “Untruncated” means the complete returned dedicated section fit the retention limit; it does not prove that every qualification was published or that the job is still accepting applications. No exact posting identity or normalized employer/title match for these 14 was found in the older 3,288-sighting production capture; different dates and possible naming changes prevent a market-wide novelty claim.

[Qualification review](qualification-review.json) links the actual descriptions and their hashes. Examples:

- **CFR Research Associate, U.S. Foreign Policy:** related bachelor required; master preferred, not required; one or more years of administrative/internship experience preferred, plus research/writing and Office skills. Published annual compensation $55–58k. Plausible early-career evidence with qualifications still to verify.
- **CFR Assistant Video Producer:** at least one year in video/digital media/communications; relevant degree or equivalent practical experience; demonstrated social video production and Adobe tools. $60–65k. Early-career duration, with material portfolio/technical requirements.
- **Jhpiego Communications Officer:** bachelor with 5–7 years relevant experience, USG global-health experience and communications requirements; published equivalencies require separate review. A communications title does not make it entry level.
- **Culmen Project Manager II:** relevant master, 5–10 years and DOE Q or ability to acquire it. Retained as a regional opportunity, not labelled a generalist early-career match.
- **Operationally discovered Bowhead Outreach Coordinator:** Arctic maritime/policy expertise, interagency familiarity and published citizenship/Public Trust conditions; degree preferred, no numeric duration stated. The title alone is insufficient evidence of early-career suitability.

The strongest early-career evidence in these diagnostic inventories is at CFR. The actual broad-discovery gain came from Bowhead and remains specialized. **Better policy-employer source discovery is still needed**; direct adapter success is not evidence that the engine already finds those employers automatically.

## Tests, reproducibility and limitations

All **230 tests pass**: the existing 211 plus 19 new tests. Coverage includes actual minimized DOM excerpts from five boards; stable identity and duplicate retention; variant locations; geographic negatives; pagination, caps, repeats and disabled links; real public search-form options and rotation; robots delays/failures and redirect restrictions; unknown versus verified empty; description identity and form exclusion; registry promotion; source-ledger/combined-audit retention; free index discovery from an empty synthetic registry; and sealed-record description exclusion. Synthetic test listings are explicitly labelled and are not vacancy evidence. The real excerpt fixtures are transformed metadata snippets, not full page replays; body hashes tie them to original captures.

```sh
python3 -m unittest discover -s tests -v
python3 docs/issue21/reproduce.py --check
```

[metrics.json](metrics.json), [minimized paired records](paired-regional-records.json), [other-inventory records](independent-records.json), [source/page/funnel audit](discovery-and-inventory.json), [input receipts](input-provenance.json) and [response hashes](response-provenance.json) support review. The small offline calculator reproduces counts and checks committed input hashes. Full response snapshots, all unranked captures, the original five-source input and the acquisition/strict-replay runners are retained in the task's private evidence bundle; raw application-page bodies and sealed-review contents are not committed.

Limits remain substantial: index coverage and timeouts; location options absent from some templates; only one regional option prioritized per bounded attempt; host-specific robots restrictions; JavaScript-only/custom templates; unknown totals and hiring status; staffing/repost ambiguity; uninspected off-market records; and geography outside the existing nearby-DMV fixture. Large per-page responses still obey byte caps. Collector support does not establish universal access to iCIMS or the entire DC employment market.

No credentials, paid APIs, model/evaluator platform, ranking change, scheduled search, application or merge is part of this change. The existing experimental ranking remains optional; the sealed Issue #9 packet remains closed.
