# Prospect local viewer (optional)

Prospect opens on **Browse Jobs**, a **read-only** view of jobs in its local
store. The dashboard is optional and runs separately from collection and
independent evaluation.

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

## Browse Jobs

Search across the latest public discovery sighting of every stored posting ID.
Each search word can match a job title, employer, or location. Combine search
with an employer or location substring and a source platform. Sort by recency,
employer, or title. **Reset filters** returns to the first page and clears your
selections.

Each page shows up to 10 results, the total matching posting IDs, and the
displayed range. Click **View original posting** when a shareable link is
available. The date and time show when Prospect last observed the listing in UTC.
Stored observations can be out of date, and separate posting IDs can describe
the same vacancy.

Search and sorting use the same privacy projection as the cards. The view
queries SQLite on interaction and does not auto-refresh. Use **Refresh results**
to pick up newly committed observations while keeping your filters.

For a synthetic database, set `PROSPECT_DEMO=1` before starting the app to show
the **DEMO / SYNTHETIC DATA** banner. The local `open-dashboard.command` demo
launcher can set this alongside its synthetic `PROSPECT_DATA_DIR`. Leave the flag
unset for an approved collection store.

## Monitoring

- **Overview** counts registered public discovery sources, stored polls, distinct
  posting IDs observed, and incomplete or skipped polls. Recent runs include
  only runs with a stored public discovery poll. A missing finish time means the
  run was not finalized in the store.
- **Run explorer** lets you choose a recorded run, inspect its start/finish status,
  number of polls, returned and unrepresented rows, and a filterable table of
  per-source poll outcomes.
- **Live activity** combines cumulative committed totals with the pilot's
  planned sources, dispatches, request results, and final export status.
  Received records include repeat observations and may include records without
  a sighting. Persisted posting IDs are distinct store identities.
- **Sources** shows public board and feed locators, platform, poll outcomes,
  received and unrepresented record counts, and last poll time. Received counts
  can include repeat observations.

The UI uses an existing SQLite connection opened with `mode=ro` plus
`PRAGMA query_only=ON` and uses short read transactions. SQLite may
create or update WAL coordination sidecar files even on a read-only connection.
The viewer never changes database records, creates a database, or runs migrations.

The viewer selects public discovery channels and applies Prospect's existing
metadata redaction to displayed text and links. Unsafe locators and links appear
blank. It makes **no collection network requests** and does not read raw
captures or the evaluator's sealed reference corpus.

The selected monitoring view refreshes every four seconds while the browser
session is active. Uncheck **Refresh monitoring every 4 seconds** to pause. Committed query results are
reused briefly until the database or WAL changes. Records appear as polls commit.

Live activity reads `run/cohort.csv`, `run/operations.jsonl`, and
`run/run-manifest.json` from the same explicitly configured data directory. It
projects only allowlisted fields, skips unfinished journal lines until the next
refresh, and shows the export snapshot ID once the pilot publishes the manifest.
Missing pilot files are normal for collection outside the pilot. An unfinished
dispatch or request is the last recorded state. Without a stop or final manifest
the dashboard cannot establish whether the process is still running.

Monitoring tables show up to 1,000 sources, 500 recent general polls, 100 recent
runs, and 2,000 per-run polls. Those filters apply within the displayed windows.
Poll status describes the collector's supported request. Coverage, current
vacancy status, and independent evaluation are outside the viewer's scope.

If there is no `PROSPECT_DATA_DIR`, no existing database, or no public collection
history, you will see an explicit message rather than invented results.

## Test without Streamlit

```bash
python -m unittest discover -s tests -p 'test_dashboard*.py' -v
```

Install the optional dashboard requirements before running the same command to
include the Streamlit interaction test.
