# Issue #7 — evidence-backed triage, with unresolved accuracy limits

This is a working **diagnostic triage implementation**, not a validated model of competitiveness. It separates career relevance, qualifications, transferable evidence, potential advantages and uncertainty; preserves broad discovery; and makes its mistakes reviewable. The real-record comparison does **not** establish an overall recommendation-quality improvement. A fresh, independently judged validation set is needed before relying on automatic recommendations.

## What changed

- `career_engine.triage` accepts normalized listings from any source. It does not require a board name, employer list, title whitelist, salary floor, ideology or organization-size rule.
- A cheap title/metadata/geography pass allocates a bounded description-analysis budget. Employer/source/function variety and a seeded, score-independent fifth of detailed slots protect exploration. No record is deleted to satisfy that budget.
- Description analysis extracts experience ranges and credentials with evidence fragments, modality (mandatory, preferred, unspecified), equivalence and trainable-workflow flags. A preferred JD is different from a required JD; a clearance that can be obtained is different from a current TS/SCI requirement. Exact candidate years, credential possession and specialist mastery are never invented.
- The public capability profile derives from the assignment and issue #7. It contains no candidate name, contact information, résumé, private application history, total-years calculation or undocumented achievements. It identifies assertions as user-reported rather than independently verified. The module accepts another profile mapping for future use; the CLI uses this public profile.
- `reasonable_application` means worth an application review, **not all requirements confirmed**. `stretch`, `investigate`, `qualification_gap`, `adjacent_review`, `location_review`, `closed_by_source_date` and `invalid_record` remain distinct. Missing descriptions go to investigation, not poor-fit rejection. All 58 DC/DMV index-only records in this snapshot remain there.
- Detailed analyses, shortlist, complete investigation queue, bounded uncertainty/exploration sample, duplicate flags and every input row are exported in JSON/CSV. Markdown explains posting evidence, candidate support, requirements, gaps, terms, dates and source. Invalid/repeated inputs remain auditable. Canonical discovery deduplication is unchanged; triage additionally flags exact normalized employer/title overlaps without merging distinct requisitions.
- A potential advantage is a combination of reported capabilities relevant to observed functions. It is explicitly **not evidence of being better than actual applicants**. Generic writing/research overlap cannot establish clearance, licensure, specialized professional knowledge or senior leadership.

The five source adapters and discovery/inspection logic are unchanged. The original score, selection, hold and deduplication functions are retained as the baseline and for compatibility. The new ranker runs after retrieval. No paid or optional AI integration was added; there is no charge-capable path in triage. Existing paid-search safeguards elsewhere in the repository are unchanged.

## Evidence and review design

The input is the actual earlier five-source capture at **2026-11-05T07:58:27+00:00**: 327 source identities, 149 DC/DMV labels, 95 retrieved descriptions and 232 index-only records. These are historical board observations, **not 327 distinct, suitable or currently active vacancies**. Possible cross-board duplicates remain. This assignment made no new listing requests and independently verified no employer's active vacancy status.

Two paired experiments are kept separate:

1. **Reproducible public abstract experiment:** [evaluation-input.json](evaluation-input.json) contains all 327 actual titles, employers, locations, links and provenance. For the 95 descriptions, it substitutes short, analyst-written factual abstracts. It includes original-description lengths and hashes, not full copyrighted advertisements or application/contact instructions. Both old and new implementations receive exactly the same abstract input. Abstracts are derived evidence, not synthetic jobs; their wording and omissions affect both systems.
2. **Original-text diagnostic:** both systems also ran on the same locally retained original capture and all 95 original descriptions. [raw-capture-comparison.json](raw-capture-comparison.json) records its input SHA256, identities, denominators and decisions. Raw capture is not committed. Reproduction requires that exact retained file; a new live run is a different experiment. **Public abstract results are not a substitute for original-text results.**

The [82 review labels](review-labels.json) and [55-case readable additional review](ADDITIONAL_REVIEW.md) were written before implementing the evaluator: 27 existing development cases, plus 55 additional records. The additional selection uses SHA256(seed|URL), stratified by source and description availability: up to eight described and three index-only DC/US-remote records per source outside the existing 27, plus up to four additional described assistant/aide/coordinator/internship/government-affairs titles from Senate and Union Jobs. Exact seeds and selection procedure are in the label manifest. Sparse strata produce fewer than their caps. The 55 contain 4 plausible, 6 stretch, 25 ineligible, 9 unlikely and 11 unknown judgments.

Labels are this assistant's analyst judgments from retrieved evidence, **not an independent human panel, employer judgments or hiring outcomes**. Selection is independent of model outputs, but the same analyst wrote abstracts and rules. The sample is deliberately stratified, heavily senior/legal and incomplete, and neither representative nor blind.

### Holdout integrity and first-pass failure

The additional 55 were initially held out. The [archived first-pass comparison](comparison-first-pass.json) did poorly: holdout-only top-15 proxy was **4/7** judged non-unknown versus the old **5/6**; top-30 was **7/12** versus **7/8**. These tiny denominators are selected subsets of the full ranked lists, not rankings made only within the holdout.

That audit exposed requirement-parsing errors. Corrections address negated nursing licensure, experience phrasing, preferred-section boundaries in flattened text, mixed mandatory/preferred credentials, employer descriptions mentioning attorneys, and obtainable clearances. Regression tests exercise those semantics. **The holdout was unsealed for these fixes. Final results below are audit-informed diagnostics, not untouched holdout validation.** The labels stayed fixed (SHA256 `cfb3067029fb194677b58cbc9c516f67e1007f137a4a9bf4f3b0931fb62108e8`). Do not interpret improved fitted results as out-of-sample accuracy.

## Paired results on identical original records

A positive proxy is an analyst `plausible` or `stretch` label. It means worth investigation/application review, not fully qualified. `Unknown` and unjudged cases are listed separately rather than silently treated as negatives. The percentage denominator excludes those two groups, so changes in judgment coverage can inflate apparent precision.

| Original-text diagnostic | Positive / judged non-unknown | Proxy | Labeled unknown | Unjudged | Positive / all selected |
|---|---:|---:|---:|---:|---:|
| Old top 15 | 12/13 | 92.3% | 0 | 2 | 12/15 |
| New top 15 | 12/12 | 100% | 0 | 3 | 12/15 |
| Old top 30 | 16/18 | 88.9% | 4 | 8 | 16/30 |
| New top 30 | 21/24 | 87.5% | 0 | 6 | 21/30 |

The new top 30 contains more labeled promising records, **but its judged relevance proxy falls slightly**, and six selections are unjudged. Top 15 has the same number of positive judgments, with a smaller known denominator. This supports better surfacing/visibility of some transferable work, not a proven precision gain.

The initially held-out subset within those original-text selections is still concerning after the audit: old/new top-15 positives are **5/6 versus 4/4** known judgments; old/new top-30 are **7/9 versus 7/10**. Remaining selections are outside the holdout or unknown. This does not demonstrate better generalization.

### Reproducible abstract experiment (different text representation)

| Public abstract experiment | Positive / judged non-unknown | Labeled unknown | Unjudged | Positive / all selected |
|---|---:|---:|---:|---:|
| Old top 15 | 12/13 | 1 | 1 | 12/15 |
| New top 15 | 13/13 | 0 | 2 | 13/15 |
| Old top 30 | 15/16 | 4 | 10 | 15/30 |
| New top 30 | 24/26 | 1 | 3 | 24/30 |

[comparison.json](comparison.json) contains exact identities and per-partition counts. The public example report uses these abstracts; it is a reproducible demonstration, not today's vacancy recommendations. Its stronger apparent result illustrates why abstract-only evaluation would overstate real-text readiness.

## False negatives, weak recommendations and concrete recovery

There are 10 additional-sample positives and 18 development positives. On original descriptions, the old/new top-30 misses are **3/10 versus 3/10** in the additional sample, and **9/18 versus 4/18** in development. This is selected-sample miss rate, **not labor-market recall**. All missed positives remain in the audit; none is erased. No labeled positive is in the final original-text `qualification_gap` group, but an incorrect gap classification remains possible on other jobs.

The original-text missed positives are listed in full in `raw-capture-comparison.json`. Examples:

- **JCT Legal Research Analyst:** political-science degree and research transfer, but prior congressional experience, specialist databases and cataloging require scrutiny; retained as a stretch below top 30.
- **NNU DC Labor Representative:** organizing and campaign skills transfer; nursing is not required. Retained as a stretch below top 30, with driving and organizing-depth questions.
- **Amazon Intelligence Analyst:** general analysis is insufficient for required analytical tradecraft and threat-assessment methods. Retained as a stretch below top 30; it should not become an unqualified research recommendation.
- **SIU Social Media Coordinator:** attractive communications work, but formal qualification evidence is thin; investigation rather than a confirmed poor fit.
- **British Embassy Political Support Officer, MCE operations analyst and CGI foreign-assistance consultant:** original-text qualification/conditions parsing pushes these below top 30. Clearances, specific experience and work authorization need human assessment. Missing these priorities is a remaining ranking weakness.

Recovered transferable work includes **AFL-CIO Senior Office Administrator, ATU Legal Secretary, UFCW Organizer-in-Training and Workers United's project organizer**. Their titles or old seniority filters previously hid them. **JCT Executive Assistant** remains a visible reasonable-application prospect, with the two-year equivalence route explicitly flagged rather than declared satisfied.

Remaining original-text weak top-30 stretches include the senior political-coordinator role (analyst ineligible due specialized experience), GTI Deputy Director (ten desired years plus personnel leadership), and specialist fellow/HR leadership work. The engine recognizes risks but can still overvalue broad research/writing/program overlap. The top 15 removes the labeled legal-credential mismatch seen in the old output, but not every high-ranked record has a review label. Clinical controls are **synthetic regression tests**; this five-source real benchmark does not establish clinical-role precision.

## Running it

No new credentials, model keys or dependencies are required. The final suite contains **109 passing tests**, including all 77 existing tests unchanged.

```sh
# Rank an already retrieved audit; no source/AI calls.
python -m tools.rank_jobs --input output/dc-source-audit.json \
  --top 15 --detailed-budget 200 --exploration-slots 10 \
  --output output/ranked-brief.md

# Reproduce this fixed-date public benchmark and example locally.
python -m tools.benchmark_triage --stress \
  --report output/issue7/ranked-brief.md
python -m tools.rank_jobs --input docs/issue7/evaluation-input.json \
  --as-of 2026-11-05T07:58:27+00:00 --seed 17 \
  --output output/issue7/example.md
python -m unittest discover -s tests -v
```

The ranker accepts a JSON `records` array, JSON list or CSV, with normalized `title`, `company`, `location`, `url`, `description`, `source` and optional provenance/dates/terms. Provider ID and source are optional; a URL identity is used otherwise. Empty or absent descriptions remain unknown. `qualifications_complete=false` can explicitly mark an incomplete export. Dates are screened at current UTC unless an explicit historical `--as-of` is supplied. A historical report is not a current availability check.

Outputs: Markdown brief; same-stem JSON containing every analyzed row plus complete investigation and exploration queues; `*-audit.csv` with all input rows; `*-shortlist.csv`. Reported evidence is untrusted posting text, never instructions to the engine. The ranker does not execute posting content or contact employers.

An optional `--triage-output output/ranked-brief.md` on `python -m career_engine` runs the same analysis after existing retrieval and enrichment, retaining every retrieved sighting. `--triage-detailed-budget` bounds its detailed pass. Existing reports remain for compatibility; use the evidence brief for this phase. The unranked five-board audit remains primary for source assessment. No discovery workflow or scheduler was changed or activated.

## Cost and scale

The implementation makes **zero network calls and zero LLM calls**; paid services are absent, not merely hidden behind a default. More description retrieval remains a separate, permission-aware source budget. An optional paid finalist evaluator could be added later with explicit opt-in, but none is needed or implemented here.

A repeated-actual-record stress test (synthetic unique identities, **not new vacancies**) ran 327, 5,000 and 20,000 records on local Python 3.13. It retained every row and bounded detailed analyses to 95/200/200. Observed times were approximately **0.04/0.24/0.85 seconds**, excluding JSON/CSV writing and network retrieval. These timings are environment-dependent, not performance guarantees or recommendation-quality evidence. `comparison.json` stores actual measurements; the test checks budgets/retention rather than asserting a fragile time limit.

Preliminary work, output retention and duplicate grouping scale with input volume; sorting costs roughly O(n log n). Description pattern analysis is bounded by the detailed budget and 60,000 characters per selected description. The input and full output are held in memory, so this is not yet a streaming production service. A 200-description default can defer good jobs in a much larger corpus. All deferred records remain `investigate`; process more budgeted batches rather than interpreting deferred work as rejected.

## Limitations and next assignment

Priority next steps for ranking are a new blinded human-reviewed time/source holdout, full qualification-section extraction that preserves HTML structure, and explicit scope/duration support for specialized mandatory skills. Keep a frozen test set after review instead of repeatedly tuning this same sample. The first-pass failure, final original-text/abstract divergence and remaining top-30 weaknesses prevent calling this validated competitive ranking. The default remains deterministic and reviewable; a human must confirm full requirements and evidence before applying.

Market-wide discovery expansion is a **separate assignment**: add permitted association/government-affairs/public-affairs and consulting feeds; improve House and other public-policy coverage; discover unfamiliar employers via broad structured search and then follow their public ATS postings. Add portable JobPosting/ATS normalization, freshness verification, geography/remote constraints, duplicate requisition evidence and source-yield auditing at that boundary. Investigate incomplete local descriptions before inflating listing counts. Keep employer monitoring secondary, avoid prohibited/private endpoints, and retain broad professional adjacency. None of these source expansions is implemented here.
