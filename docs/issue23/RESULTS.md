# Issue 23 — DC foundation results

## Shared-capture comparison

Before: `b0fbaabef2f3390fc36519bf6d09b1225d8aba5e` (unaltered `career_engine/ats.py`, module hash recorded in metrics). After: this PR. Both arms replay identical URL/parameter captures, with zero live fallback: 30 requests, 64 MB total, **8,000,000 bytes per response**, and 3 optional geography-selected details per board. Neither arm ranks jobs or selects details by suitability.

| Actual board | Retained postings before → after | DC/DMV descriptions before → after | All descriptions before → after | Requests (detail GETs) before → after | Observed bytes before → after | After outcome |
|---|---:|---:|---:|---:|---:|---|
| National Nurses United (modest) | 14 → 14 | 3/3 → 3/3 | 3 → 14 | 5 (3) → 2 (0) | 52,527 → 203,598 | Full content list; metadata/descriptions complete for returned inventory |
| Acorn Health (supplemental modest board) | 126 → 126 | 3/9 → 9/9 | 3 → 126 | 5 (3) → 2 (0) | 192,316 → 3,387,555 | Full content list; returned metadata complete; normalized description truncation is separately reported |
| Anduril Industries (oversized) | 2,593 → 2,593 | 3/238 → 3/238 | 3 → 3 | 5 (3) → 6 (3) | 2,786,611 → 10,847,539 | `partial_failure`; `metadata_fallback_bounded_details`; metadata complete, descriptions incomplete |

**Verified tradeoff:** fewer detail requests on modest boards, with more bytes because all bodies are retrieved. Acorn gains six regional descriptions; NNU and the oversized board gain no regional descriptions at this detail budget. The oversized attempt consumes 8,060,928 bytes (one streaming chunk past the cap), is closed, and falls back once. No posting identities from either before arm are lost. No failed oversized response becomes an empty success. These are source observations, not proof of current vacancies, suitability or regional employment-market recall.

Primary capture: **2026-11-06 21:10:02–21:10:11 UTC** (14:10 Denver); supplemental capture: **21:08:46–21:08:51 UTC**. Exact URLs, parameters, timestamps, byte counts, complete/prefix flags and SHA-256 hashes are in [response-provenance.json](response-provenance.json) and [supplemental-response-provenance.json](supplemental-response-provenance.json). Metrics and request outcomes: [metrics.json](metrics.json), [supplemental-metrics.json](supplemental-metrics.json). The oversized prefix is deliberately **not** treated as valid JSON.

Board URL evidence: NNU/Acorn were already observed in the repository's Issue 17/21 captures and coverage catalogue. The Anduril URL was observed in a [public posting reference](https://tracker.mccoy.io/postings/anduril%3A5281223007), then verified through the documented public board API, which returned its employer name and inventory. Initial diagnostic boards D.H. Pace (402 records, 3.96 MB content response including board metadata), Curaleaf (234, 6.00 MB) and AB InBev (58, 0.61 MB) did not exceed the limit; their original captures were retained locally. They are not represented as oversized tests. No collector budget was raised.

Raw public response bodies, incomplete prefixes, and complete unranked before/after JSON for the paired and supplemental comparisons are retained in the task evidence archive. They are not added to the repository. Reproduce from the archive directory:

```sh
python -m tools.measure_greenhouse --captures PATH/issue23-paired-captures --output /tmp/issue23-metrics.json
python -m tools.measure_greenhouse --captures PATH/issue23-large-captures --boards nationalnursesunited,acornhealth --output /tmp/issue23-supplemental.json
python -m unittest discover -s tests -v
```

## State integrity

Registry restore now filters completed successful runs on the current branch, newest first, up to 100 successful runs. Each candidate downloads into a fresh temporary directory. Complete JSON, lossless structural validation, manifest completion, producer run/attempt, byte count, SHA-256 and retained board/history counts must agree before atomic replacement. Corrupt, interrupted, missing or failed candidates leave active state untouched and permit trying an older successful candidate.

For compatibility, an unsealed legacy v1 snapshot is eligible only if its producing commit contains the exact pre-Issue-23 workflow blob `8f43f1f9257547fa1dadb002cf7c2dddc849ac02`. It must pass full structural validation; diagnostics label its weaker `legacy_v1_structural_validation`. A new producer missing its manifest cannot downgrade to legacy. Existing board, posting and source-status history is preserved, including legacy iCIMS link evidence. Invalid state is rejected rather than silently sanitized/truncated.

Mutable registry writes occur atomically **after** observation files and reports finish. The workflow seals/uploads a separate state snapshot only on pipeline success. A deliberately allowed **PARTIAL COLLECTION** can still produce a valid snapshot; failed-source states and surviving observations remain explicit. Reports upload under `always()` independently. The next run publishes `output/registry-restore.json` and a step-summary diagnostic with `state_restored_from`, rejected attempts, restore failure or explicit cold start.

**Remaining limit:** reports expire after 30 days; registry snapshots after 90 days. Quiet periods longer than retention can still lose history. The 100-run search and branch scope are also explicit bounds. No durable cloud state, scheduler or new infrastructure is introduced.

## USAJOBS configuration

Read-only GitHub Actions settings inspection on **November 6, 2026** showed no repository/environment secrets and no repository variables. The local environment also lacks both credentials. [usajobs-configuration.json](usajobs-configuration.json) records `configured=false`, `status=not_collected`, zero requests and zero jobs. Credentialed live collection remains **unverified**.

Only the **manual DC regional expansion** (`source_mode=dc`, `expanded_ats=true`) now calls the existing USAJOBS collector when these are configured:

- Repository secret `USAJOBS_API_KEY`: user-granted authorized API key.
- Repository secret `USAJOBS_USER_AGENT`: email used to register that key.
- Repository variable `USAJOBS_TERMS_ACCEPTED=true`: user confirmation of authorized API use and applicable terms. The code never accepts terms or changes account settings.

The unchanged collector query is Washington, DC, radius 35 miles, newest open date, 100 results/page. `source_pages` bounds it (default 6, range 1–20), with one-second pacing, 8 MB/response, 64 MB and 600-second adapter bounds, no retries, and no redirect forwarding of credentials. Earlier yielded rows survive later failure. Results, public raw fields, official announcement links, retrieval dates, source status and result-window limits enter the **separate regional and combined audits**, without modifying the original five-source files. Headers/credentials never enter output artifacts. Other markets and legacy/non-DC modes do not call this handoff. No live USAJOBS collection was claimed or fabricated.

## Validation and references

**252 tests passed**, including 22 new regressions covering batch decoding and literal HTML text, oversized fallback, denials/global limits, preserved rows, mocked prior runs/artifacts, corrupted/missing manifests, legacy migration, atomic/report-write failures, explicit restore diagnostics, credential/terms gates, header privacy, bounded USAJOBS failures, source-ledger attribution and mode isolation. `git diff --check` passed. Existing tests verify the frozen ranker and holdout commitments; no holdout bodies were opened or evaluated.

Official contracts: [Greenhouse list jobs](https://docs.greenhouse.io/job-board.html#list-jobs), [GitHub CLI run list](https://cli.github.com/manual/gh_run_list), [USAJOBS authentication](https://developer.usajobs.gov/guides/authentication), [USAJOBS terms](https://developer.usajobs.gov/guides/terms-of-service). No AI ranking, original five-source collector, iCIMS adapter, paid service, application, schedule or merge changes.
