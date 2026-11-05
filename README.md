# DC Career Discovery Engine

Private, discovery-first job search prototype. Its objective is to discover unfamiliar DC-area and US-remote roles across research, policy, government affairs, advocacy, communications, political work, consulting-adjacent roles and operations—not merely monitor a preselected employer list.

## Current GitHub deployment

This is a **small cloud-compatible implementation** of the earlier local Release 1, not a complete file-for-file migration. It supports:

- Brave Search (general-web discovery, requires the `BRAVE_API_KEY` repository secret)
- Remotive (no API key, limited remote-work coverage; attribution preserved)
- CSV import when run locally
- SQLite history across GitHub Actions runs when history artifacts remain available
- Relevance heuristics, coarse geographic and seniority screening, conservative deduplication, and a Markdown + CSV ranked report
- Five unit tests run automatically via GitHub Actions

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

## Data and limitations

- Reports are workflow artifacts retained for 30 days. SQLite history is carried across successful runs by downloading the latest `career-history` artifact; history artifacts expire after 90 days. This is not a durable production database.
- Source matching is imperfect, especially for search results without structured employer or location data.
- This prototype **does not** crawl arbitrary sites, independently verify that jobs are still open, produce full competitive-advantage assessments, sync Notion, auto-apply, or trigger recurring paid API calls.
- The project does not contain personal resumes or private application tracking records.
- Respect source licensing, robots directives and API quotas. Do not bypass login, paywall or anti-bot restrictions.

## Development priorities

1. Verify broad Brave search yield against the weak Remotive-only benchmark.
2. Reintegrate full-page extraction, Adzuna/USAJOBS, and specialty-policy job-board adapters from the local prototype.
3. Add applied-history deduplication, source-yield reports and false-negative sampling.
4. Introduce local semantic reranking and then deeper competitive-fit reviews.
5. Replace artifact-only persistence with durable storage and only then add schedules.

The full earlier project package remains available from the ChatGPT conversation as `career_discovery_engine_github_ready.zip`; it has not yet been fully reproduced here.
