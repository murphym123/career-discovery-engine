# Prospect local viewer (optional)

An intentionally simple, **read-only** Streamlit UI for inspecting the data
Prospect has actually persisted. This lives in the producer repository, but is
not part of its collectors, GitHub workflows, or independent evaluator.

## Start on your Mac

Run from the repository root. Your existing private data directory must contain
`prospect.sqlite`. The dashboard does not create one.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r dashboard/requirements.txt
export PROSPECT_DATA_DIR="/your/private/path/to/prospect-data"
python -m streamlit run dashboard/app.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --browser.gatherUsageStats false
```

Open **http://127.0.0.1:8501** in Safari. Press **Ctrl+C** in Terminal to stop.
If you already have the repo's Python environment, you can install the dashboard
requirements into that instead of creating a new virtual environment.

> **Keep it local.** Streamlit is a web server, not an authenticated desktop app.
> Do not bind to `0.0.0.0`, expose it through tunnels, or put it on a public host.
> The data directory and browser session are intended for the approved Mac only.

## What it shows

- **Overview:** public source count, polls, distinct posting IDs observed,
  incomplete/skipped polls, and recent collection runs.
- **Sources:** source board/feed locators, platform, poll outcomes, raw record
  counts, unrepresented record counts and last polling time.
- **Listings:** most recent observed public-discovery sighting for each posting:
  title, employer, location, source, timestamps and public job link. Text filter.

The UI uses an existing SQLite connection opened with `mode=ro` plus
`PRAGMA query_only=ON`. It only queries discovery channels with
`retention_class='public'`, and never opens raw captures or the evaluator's
sealed reference corpus. It makes **no collection network requests**, changes
no storage state, and does not trigger the live pilot.

The table windows currently cap sources at 1,000, recent polls at 500,
runs at 100, and displayed distinct listings at 2,000. They are viewer limits,
**not coverage denominators**. Poll success does not prove that a career site was
fully covered. A stored listing is not proof that it is still open. Evaluation
metrics are not shown because independently approved safe outputs are not wired
into this first version.

If there is no `PROSPECT_DATA_DIR`, no existing database, or no public collection
history, you will see an explicit message rather than invented results.

## Test without Streamlit

```bash
python -m unittest tests.test_dashboard -v
```
