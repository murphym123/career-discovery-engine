# Issue #9 — optional cloud shortlist and quarantined review

The manual DC workflow can now add an **EXPERIMENTAL — NOT INDEPENDENTLY VALIDATED** ranking beside its complete unranked audit. The option defaults to **false**. Ranking reuses the audit JSON; it does not collect sources again, call a model, use a paid API, or replace the source audit. The unranked JSON/CSV/Markdown remains the source of truth.

**Independent validation is pending.** The user confirmed that no independent human reviewer is currently available. A 40-listing blinded original-text packet is prepared separately, with blank labels and no model outputs. Nothing in this assignment establishes that a qualified human prefers the ranked list or that the ranker is validated.

## Manual GitHub use

After this workflow change is merged by a reviewer:

1. Open **Actions → DC career discovery → Run workflow**.
2. Keep **source_mode = dc** and **allow_paid_search = false**.
3. Leave **experimental_ranking = false** for the existing unranked report. Explicitly check it to add the experimental shortlist.
4. Keep the existing detail-page budget appropriate to the search. Ranking reads only descriptions already retrieved. It cannot infer qualifications from an index-only record.
5. Download **career-report-<run ID>**. The original three `dc-source-audit` files are still present. The experimental files are in `experimental-ranking/`.

The new checkbox may not appear on the default branch until this PR is merged; this assignment does not merge it. Other source modes and paid-search safeguards are unchanged. The ranking option is ignored outside DC mode. No schedule was added or enabled.

Added artifacts:

- `README.md`: prominent experimental warning and file guide.
- `experimental-ranked-brief.md` and `.json`: explained shortlist and all-record structured audit.
- `experimental-ranked-brief-audit.csv`: every input record, including rejected/unknown rows.
- `experimental-ranked-brief-shortlist.csv`: selected rows, with experimental/validation-status columns.
- `experimental-review-queue.json` and `.csv`: complete investigation queue plus the bounded exploration sample; not recommendations.
- `run-status.json`: input hash, capture time, ranker version/hash, counts and success/partial/failure state.

The ranking step runs after a partial retrieval failure when an audit exists, labels partial input clearly, and preserves the source files. If ranking itself fails, its failure manifest and original source artifacts are still eligible for upload through the existing `always()` upload step. Cancellation skips ranking. No new credentials or dependencies are required.

## New records and provenance

Re-fetching the five default indexes produced no unseen records in the three indexes probed. Merely changing an inspection seed would not create an unseen test set. We therefore used **one-off evaluation retrieval**, without adding production discovery sources or an employer allowlist to the engine.

The first additional cohort contained 79 previously unevaluated board records from public On Think Tanks pagination and WRAG's public index: 73 descriptions had already passed their supplied closing dates; six WRAG description requests returned HTTP 500. These are useful freshness/incompleteness controls, not evidence of current suitable openings. The WRAG advertised feed also failed; no bypass or repeated error requests were used. The archived cohort is excluded from the current-job quality diagnostic.

A separate current-public-ATS cohort came from nine employer boards discovered through broad DC research/consulting/program/advocacy searches: ATI Advisory, Breakwater Strategy, BGBx Consulting, Capital Technology Group, ACOG, The AI Policy Network, ZERO TO THREE, Alliance Defending Freedom and Charles River Associates. The [documented Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) permits unauthenticated GET requests and exposes original descriptions with `content=true`. Only public GETs were made; no application endpoints or private APIs were used.

Nine requests served **207 published records**. A deterministic employer-balanced sampling frame took up to 12 target-geography/remote records and two controls per employer, across all functions and levels: 81 records. Prior URLs, employer/title pairs, provider identities and original-description hashes were checked against the earlier 327-record evaluation and the 79 archived controls. One known employer/title match was excluded. These are newly retrieved/unseen records, **not a claim that every posting was newly published**.

The initial 40/40 split contained three same-employer/title location variants across the partitions. A stricter overlap check detected them. Those three review entries were withdrawn and replaced with previously unsampled records from the same API responses, before any human review. The replacement packet was reordered and resealed. The diagnostic 40 stayed fixed; no ranking rule was changed. The current review and diagnostic partitions have **zero exact URL or normalized employer/title overlap**. The withdrawn packet is not the future validation set.

The final current review has 40 unscored records, and the diagnostic partition has 40 scored records. This sampling frame deliberately spans levels and adjacent work; it is **not a random or representative sample of the DC labor market**. Consulting/technical work and a few employers are overrepresented. It cannot measure market-wide recall or validate generalization across unseen employers. Exact hashes, HTTP outcomes and counts are in [provenance.json](provenance.json); original unsealed-record metadata is in [diagnostic-metadata.json](diagnostic-metadata.json).

## Blinded packet and leakage protection

The primary packet is delivered outside Git under ignored `output/issue9/current-blinded-review/`. It contains:

- Shuffled listings with opaque review IDs, employer, raw location, original link, observed/updated dates and original descriptions including qualifications.
- A non-identifying candidate brief: political-science BA, political/opposition/public-records research, internship experience, outreach/recruitment, CRM, events, educational programs, writing/editing, operations, teaching, advocacy and some AI-policy work. Exact years and unestablished credentials remain unknown.
- Blank `labels.csv` and a reviewer attestation. No rankings, scores, decisions, recommendations, model-generated requirement summaries or diagnostic answers appear in the packet.

Only commitments and protocol metadata are committed under [review_holdout/issue9](../../review_holdout/issue9/README.md), outside development datasets and test fixtures. No original job descriptions, private résumé, personal contact details or review labels from the packet are committed. CI checks procedural metadata and synthetic cases; it never reads or predicts the quarantined listing bodies. Hash commitments establish a fixed packet; they are not encryption or access control.

`tools.prepare_blind_review` does not import the ranker. It excludes known records, uses metadata-only geographic/source/employer sampling and selects a single representative per exact employer/title for review sampling only. This does not merge or remove any production discovery records. It refuses to overwrite an existing packet or human labels. `tools.diagnose_triage` refuses any sealed URL or employer/title pair. The core ranker SHA256 remains **22eeaea408d245af83dc476ea734286f1ac773cbbb7a5b7f0d71d2e3e0fa6266**, identical to the start of this assignment.

For later independent review: assess the packet without running the model or reading predictions on it; record fit labels, mandatory evidence, gaps and uncertainty; freeze labels and complete the reviewer attestation before unblinding. Only then run a separately authorized holdout evaluation. Do not turn those records into regression fixtures or tune on the results and still call them untouched. A qualified-human preference comparison between the ranked and full unranked outputs also remains to be collected after that review.

## Diagnostic results — assistant labels, not validation

All 40 **unsealed** current records were read with their original descriptions. Labels were frozen before viewing diagnostic rankings, but the same assistant authored the model, so they are not independent judgments. There are 3 plausible, 3 stretch, 21 ineligible, 12 unlikely and 1 unknown labels. The [readable diagnostic review](DIAGNOSTIC_REVIEW.md) includes evidence cautions, routes and a random rejected sample. Both systems receive identical original text. No factual abstracts replace the diagnostic descriptions.

Positive means plausible or stretch; unknown is separate. All records in this diagnostic have a label. The old system selected fewer rows than the requested limit; neither system is padded to manufacture a top 30.

| Requested list | Actually selected | Positive / known | Proxy | Unknown | Unjudged | Labeled positives / selected |
|---|---:|---:|---:|---:|---:|---:|
| Old top 15 | 9 | 3/8 | 37.5% | 1 | 0 | 3/9 |
| Experimental top 15 | 15 | 6/14 | 42.9% | 1 | 0 | 6/15 |
| Old top 30 | 9 | 3/8 | 37.5% | 1 | 0 | 3/9 |
| Experimental top 30 | 17 | 6/16 | 37.5% | 1 | 0 | 6/17 |

The experimental output surfaces all **6/6** labeled positives versus **3/6** previously, but adds **10 known negative judgments and one unknown** in its 17-row shortlist. Its judged top-30 proxy is unchanged and poor. These selected-sample counts are not precision estimates for future jobs, hiring odds, or labor-market recall. Independent-review denominators are **zero/pending**, not the assistant denominators above.

Concrete findings:

- AIPN Junior Associate and ACOG Program Administrator remain visible as plausible early-career roles; Membership Data Specialist is a stretch because CRM experience alone does not establish analytics/degree/duration requirements.
- The experimental system recovers ACOG Membership Data Specialist, AIPN Digital Organizing Director and CRA Financial Economics Associate as diagnostic stretches outside the old top 30. Material scope/degree/quantitative gaps still need review.
- AIPN's **General Interest** is incorrectly promoted to reasonable application despite being a prospect submission, not a defined vacancy. Its public-API prospect marker is present in the source metadata. This is a suspicious false positive, not a new opening.
- A technical incident-recovery role is also marked reasonable, while accounting, senior capture and product leadership roles remain stretches despite substantial unsupported requirements. Broad research/operations wording still misleads the ranker. The new data were not used to patch those rules in this assignment.
- BGBx doctorate-titled positions illustrate why title-only screening is unsafe: their actual descriptions allow a BA plus specialist experience as an alternative. That does not establish this candidate's specialist experience. A desired advanced degree or certification is not a mandatory disqualifier.
- A seeded random sample of **10 of the 23 records outside the experimental top 30** includes senior, technical, legal and clinical roles. All ten carry negative diagnostic judgments. This small selected sample cannot establish that all rejected jobs are correctly routed. Full identities and rationales are retained.

Freshness, fit and confidence remain separate. A public employer-managed ATS response confirms that a record was served at retrieval time; it does not independently confirm that an application is still accepted. API `updated_at` is not publication date. Missing descriptions and state-only Maryland locations remain unknown, while nearby Camp Springs/Silver Spring are regional evidence. `moderate_text_only` refers to evidence availability, not confidence of being competitive.

## Verification and practical limits

**124 executable tests pass**, including all 109 existing tests unchanged. New tests execute the actual workflow Bash with synthetic audit data and verify one collection call followed by offline ranking, all artifacts, warning/status columns, input-byte preservation, failure/partial handling, geographic routing, verification status, default-off/DC-only gating, no schedule, and no collection/paid path in the ranking step. Synthetic packet tests verify allowlisting, blank labels, non-overwrite behavior, novelty exclusions and partition overlap protection. They do not measure real-vacancy quality.

```sh
python -m unittest discover -s tests -v
python -m tools.publish_experimental --input output/dc-source-audit.json \
  --output-dir output/experimental-ranking
```

The workflow step is tested locally using its exact Bash and in normal CI. A live manual discovery workflow was not dispatched for this change; no cloud search or paid mode was silently enabled. The GitHub connector available for this assignment does not expose a new-workflow dispatch action. After review/merge, the user can perform the first cloud opt-in run with the checkbox above.

Diagnostic reproduction requires the exact retained original-text input, whose SHA256 is recorded in `diagnostic-results.json`; it is not committed as a full-text corpus. The current sealed packet must **not** be supplied to the diagnostic command. Public metadata and assistant judgments are inspectable in this PR; they are not interchangeable with original descriptions or human validation.

The user should keep the full unranked audit as their primary review source. This phase supplies a convenient experimental aid and a clean future-review packet, **not evidence that the ranked shortlist is preferable**. Future parser/model changes need a separate development partition and a newly untouched test partition. Market-wide source expansion remains a separate assignment.
