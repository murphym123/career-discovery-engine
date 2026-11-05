# DC Career Discovery Engine

Private, discovery-first job search prototype. Its objective is to discover unfamiliar DC-area and US-remote roles across research, policy, government affairs, advocacy, communications, political work, consulting-adjacent roles and operations—not merely monitor a preselected employer list.

## Current GitHub deployment

This is a **small cloud-compatible implementation** of the earlier local Release 1, not a complete file-for-file migration. It supports:

- Brave Search (general-web discovery, requires the `BRAVE_API_KEY` repository secret)
- Remotive (no API key, limited remote-work coverage; attribution preserved)
- CSV import when run locally
- SQLite history across GitHub Actions runs when history artifacts remain available
- Relevance heuristics, coarse geographic and seniority screening, conservative deduplication, and a Markdown + CSV ranked report
- Automated unit and offline pipeline tests via GitHub Actions
- Optional bounded HTML extraction using public schema.org JobPosting metadata, with robots checks and extraction status tracking

The broader local prototype also has optional Adzuna/USAJOBS feeds, full-page HTML extraction, more extensive ranking and source configurations; those are **not yet ported into this GitHub cloud runner**. Scores are triage heuristics, not predictions of interviews or offers. Neither version guarantees a posting remains open.

## Running from GitHub (no Terminal)

1. Open the [Actions page](https://github.com/murphym123/career-discovery-engine/actions).
2. Select **DC career discovery** in the left sidebar.
3. Click **Run workflow** and choose `remotive` for a free source connectivity test.
4. Open the finished workflow run and download the `career-report-...` artifact. Open `live_brief.md` or the accompanying CSV.

The first run may find few compelling DC jobs: Remotive is not a DC policy job board.

## Enabling open-web search

In the repository go to **Settings → Secrets and variables → Actions → New repository secret**. Add the name `BRAVE_API_KEY` and its value from the Brave Search API dashboard. Do **not** paste it into source code or ChatGPT.

Then run **DC career discovery** using `source_mode=brave` (or `both`). The default `query_budget=6` requests up to six search queries, capped at 20 returned results each. Brave may charge according to your account. No workflow is scheduled automatically.

An optional `extract_budget` input controls how many public search-result pages are inspected for schema.org JobPosting metadata. Default is **0**; for a small experiment, use 5–10. The feature respects robots directives, declines private/non-HTTPS URLs, limits redirects, and records failures. It does not bypass site access restrictions or confirm that a job is still accepting applications. Search results without job metadata are shown separately as **unverified web leads**, not confirmed vacancies. Many genuine jobs lack structured metadata; the extraction stage is deliberately incomplete.

## Data and limitations

- Reports are workflow artifacts retained for 30 days. SQLite history is carried across successful runs by downloading the latest `career-history` artifact; history artifacts expire after 90 days. This is not a durable production database.
- Source matching is imperfect, especially for search results without structured employer or location data.
- The extraction stage reads only a bounded number of public HTML pages and uses JobPosting metadata; it does not traverse entire sites or independently verify that jobs are still open.
- This prototype **does not** produce full competitive-advantage assessments, sync Notion, auto-apply, or trigger recurring paid API calls.
- The project does not contain personal resumes or private application tracking records.
- Respect source licensing, robots directives and API quotas. Do not bypass login, paywall or anti-bot restrictions.

## Development priorities

1. Verify broad Brave search yield against the weak Remotive-only benchmark.
2. Expand beyond schema.org metadata to robust extraction and reintegrate Adzuna/USAJOBS and specialty-policy job-board adapters from the local prototype.
3. Add applied-history deduplication, source-yield reports and false-negative sampling.
4. Introduce local semantic reranking and then deeper competitive-fit reviews.
5. Replace artifact-only persistence with durable storage and only then add schedules.

The full earlier project package remains available from the ChatGPT conversation as `career_discovery_engine_github_ready.zip`; it has not yet been fully reproduced here.
