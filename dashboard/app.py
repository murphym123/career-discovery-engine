"""Minimal local, read-only GUI for Prospect's persisted collection history.

Launch from the repository root with:
  python -m streamlit run dashboard/app.py --server.address 127.0.0.1
"""
import os
import sqlite3

import streamlit as st

from dashboard.activity import activity
from dashboard.browse import render_browse
from dashboard.queries import (
    collection_totals, database_path, open_store, overview, polls, runs, sources,
)


st.set_page_config(page_title="Prospect | Local viewer", page_icon="🔎", layout="wide")
st.title("Prospect")
if os.getenv("PROSPECT_DEMO") == "1":
    st.warning("DEMO / SYNTHETIC DATA")

section = st.sidebar.radio(
    "View", ["Browse Jobs", "Overview", "Run explorer", "Sources", "Live activity"]
)
auto_refresh = st.sidebar.checkbox(
    "Refresh monitoring every 4 seconds", value=True, disabled=section == "Browse Jobs",
)
st.sidebar.caption("No collectors or evaluations are launched from this interface.")

data_dir = os.environ.get("PROSPECT_DATA_DIR", "").strip()
if not data_dir:
    st.info("Set PROSPECT_DATA_DIR to your existing private Prospect data directory, then restart.")
    st.code('export PROSPECT_DATA_DIR="/path/to/private/prospect-data"', language="bash")
    st.stop()

def store_revision(data_dir):
    path = database_path(data_dir)
    revision = []
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            info = candidate.stat()
            revision.append((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        except FileNotFoundError:
            revision.append(None)
    return tuple(revision)


@st.cache_data(max_entries=16, ttl=60, show_spinner=False)
def read_view(data_dir, section, revision, run_id=None):
    # The database and WAL signature invalidates cached results after commits.
    with open_store(data_dir) as connection:
        if section == "Overview":
            return overview(connection), runs(connection)
        if section == "Run explorer":
            return runs(connection) if run_id is None else polls(connection, limit=2000, run_id=run_id)
        if section == "Sources":
            return sources(connection), polls(connection)
        return collection_totals(connection)


def render_activity():
    progress = activity(data_dir)
    st.subheader("Live activity")
    st.write(progress["status"])
    if progress["reason"]:
        st.caption(f'Recorded reason: {progress["reason"]}')
    if progress["snapshot"]:
        st.caption(f'Export snapshot: {progress["snapshot"]}')
    if not progress["journal_present"]:
        st.info("No readable pilot journal is available in this data directory.")
    for notice in progress["notices"]:
        st.caption(notice)

    try:
        totals = read_view(data_dir, "Live activity", store_revision(data_dir))
    except FileNotFoundError:
        st.info("The collection database has not been created in this directory yet.")
    except (OSError, sqlite3.DatabaseError, ValueError):
        st.warning("Committed totals are temporarily unavailable.")
    else:
        a, b, c = st.columns(3)
        a.metric("Received records", totals["received"])
        b.metric("Persisted sightings", totals["sightings"])
        c.metric("Persisted posting IDs", totals["posting_ids"])
        st.caption(
            "Totals cover public discovery in this store. A posting can have multiple sightings. "
            "Different posting IDs can describe the same vacancy."
        )

    if progress["sources"]:
        st.subheader("Source progress")
        st.dataframe(progress["sources"], hide_index=True)
    if progress["requests"]:
        st.subheader("Request activity")
        st.dataframe(progress["requests"], hide_index=True)
    st.caption("Unfinished activity shows the last recorded state. The dashboard cannot detect a silent exit.")


@st.fragment(run_every=4 if auto_refresh else None)
def render():
    if section == "Live activity":
        render_activity()
        return

    try:
        revision = store_revision(data_dir)
        if section == "Overview":
            counts, recent = read_view(data_dir, section, revision)
        elif section == "Run explorer":
            recent = read_view(data_dir, section, revision)
            if recent:
                run_labels = {
                    row["run_id"]: (
                        f'{row["started_at"]}  ·  {row["arm"]}  ·  '
                        f'{"finished" if row["finished_at"] else "unfinished"}'
                    )
                    for row in recent
                }
                run_id = st.selectbox(
                    "Inspect a collection run", list(run_labels),
                    format_func=run_labels.get, key="inspected_run",
                )
                selected_run = next(row for row in recent if row["run_id"] == run_id)
                run_poll_rows = read_view(data_dir, section, revision, run_id)
            else:
                selected_run, run_poll_rows = None, []
        elif section == "Sources":
            source_rows, poll_rows = read_view(data_dir, section, revision)
    except FileNotFoundError:
        st.error("No prospect.sqlite found in PROSPECT_DATA_DIR.")
        return
    except (OSError, sqlite3.DatabaseError, ValueError):
        st.error("Unable to read the Prospect store. Check its permissions and schema version.")
        return

    if section == "Overview":
        a, b, c, d = st.columns(4)
        a.metric("Registered public sources", counts["sources"])
        b.metric("Recorded polls", counts["polls"])
        c.metric("Distinct posting IDs seen", counts["unique_jobs"])
        d.metric("Incomplete / skipped polls", counts["incomplete_polls"])
        st.caption(f'Last collection poll: {counts["last_poll"] or "none recorded"}')
        st.subheader("Recent collection runs")
        if recent:
            st.dataframe(
                [{key: value for key, value in row.items() if key != "run_id"}
                 for row in recent],
                hide_index=True,
            )
            st.caption("A blank finish time means the run was not finalized in the store.")
        else:
            st.info("No public discovery collection runs are stored yet.")
        st.caption(
            "Registered sources may have zero polls. Posting counts reflect stored IDs "
            "and do not measure market coverage, active vacancies, or recall."
        )

    elif section == "Run explorer":
        st.subheader("Collection run explorer")
        if not selected_run:
            st.info("No stored public discovery runs yet. The dashboard does not launch collectors.")
        else:
            completed = bool(selected_run["finished_at"])
            st.caption(
                f'Started: {selected_run["started_at"]}  ·  '
                f'Ended: {selected_run["finished_at"] or "not finalized"}  ·  '
                f'Trigger: {selected_run["trigger"]}'
            )
            if not completed:
                st.warning(
                    "This run has not been finalized in the store. "
                    "Its recorded polls are not necessarily a complete run history."
                )
            a, b, c, d = st.columns(4)
            a.metric("Polls recorded", selected_run["polls"])
            b.metric("Rows received", selected_run["rows_received"])
            c.metric("Unrepresented rows", selected_run["unrepresented"])
            d.metric("Incomplete / skipped", selected_run["incomplete_polls"])

            st.subheader("Poll outcomes")
            if run_poll_rows:
                status_options = sorted({row["status"] for row in run_poll_rows})
                included = st.multiselect(
                    "Filter poll status", status_options, help="Leave empty to show all statuses."
                )
                filtered_polls = [
                    row for row in run_poll_rows if not included or row["status"] in included
                ]
                st.dataframe(filtered_polls, hide_index=True)
                st.caption(
                    f"Showing {len(filtered_polls)} of {len(run_poll_rows)} recent recorded polls "
                    "(maximum 2,000). Counts may include repeated postings."
                )
            else:
                st.info("This run has no public discovery polls.")
            st.caption("Use Live activity for planned sources, request outcomes, and export status.")

    elif section == "Sources":
        st.subheader("Source boards and feeds")
        if source_rows:
            platforms = sorted({row["platform"] or "unknown" for row in source_rows})
            selected = st.multiselect("Source table platform", platforms, default=platforms)
            shown = [
                row for row in source_rows
                if (row["platform"] or "unknown") in selected
            ]
            st.caption(
                f"Showing {len(shown)} of {len(source_rows)} public discovery channels "
                "(up to 1,000 most recently polled)."
            )
            st.dataframe(shown, hide_index=True)
        else:
            st.info("No public discovery sources are stored yet.")
        st.subheader("Recent polling results")
        if poll_rows:
            st.dataframe(poll_rows, hide_index=True)
        else:
            st.caption("No polling results recorded.")
        st.caption(
            "Received counts include repeats across polls. Unrepresented records were "
            "received without a stored sighting. An 'ok' poll completed the collector's "
            "supported request."
        )


if section == "Browse Jobs":
    try:
        render_browse(data_dir, store_revision(data_dir))
    except FileNotFoundError:
        st.error("No prospect.sqlite found in PROSPECT_DATA_DIR.")
    except (OSError, sqlite3.DatabaseError, ValueError):
        st.error("Unable to read the Prospect store. Check its permissions and schema version.")
else:
    render()
