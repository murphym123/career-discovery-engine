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
python -m pip install -r requirements.txt -r dashboard/requirements.txt
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

- **Overview** counts registered public discovery sources, stored polls, distinct
  posting IDs observed, and incomplete or skipped polls. Recent runs include
  only runs with a stored public discovery poll. A missing finish time means the
  run was not finalized in the store.
- **Run explorer** lets you choose a recorded run, inspect its start/finish status,
  number of polls, returned and unrepresented rows, and a filterable table of
  per-source poll outcomes. It does not display unattempted planned boards.
- **Sources** shows public board and feed locators, platform, poll outcomes,
  received and unrepresented record counts, and last poll time. Received counts
  can include repeat observations.
- **Listings** shows the most recent public discovery sighting per stored posting
  ID. You can filter the displayed rows by title, employer, location, or source.

The UI uses an existing SQLite connection opened with `mode=ro` plus
`PRAGMA query_only=ON` and reads one consistent snapshot per view. SQLite may
create or update WAL coordination sidecar files even on a read-only connection.
The viewer never changes database records, creates a database, or runs migrations.

The viewer selects public discovery channels and applies Prospect's existing
metadata redaction to displayed text and links. Unsafe locators and links appear
blank. It makes **no collection network requests** and has no access to raw
captures, pilot transport logs, or the evaluator's sealed reference corpus.

The tables show up to 1,000 sources, 500 recent general polls, 100 recent runs,
2,000 per-run polls, and 2,000
distinct posting IDs. Filters apply within those windows. Listings with different
stored IDs can still refer to the same vacancy. Poll status describes the
collector's supported request, and listings record past observations. Coverage,
current vacancy status, and independent evaluation are outside the viewer's scope.

If there is no `PROSPECT_DATA_DIR`, no existing database, or no public collection
history, you will see an explicit message rather than invented results.

## Test without Streamlit

```bash
python -m unittest discover -s tests -p 'test_dashboard.py' -v
```
