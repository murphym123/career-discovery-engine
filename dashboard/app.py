"""Minimal local, read-only GUI for Prospect's persisted collection history.

Launch from the repository root with:
  python -m streamlit run dashboard/app.py --server.address 127.0.0.1
"""
import os
import sqlite3

import streamlit as st

from dashboard.queries import listings, open_store, overview, polls, runs, sources


st.set_page_config(page_title="Prospect | Local viewer", page_icon="🔎", layout="wide")
st.title("Prospect")
st.caption("Local collection viewer · read-only · public discovery records only")

section = st.sidebar.radio("View", ["Overview", "Run explorer", "Sources", "Listings"])
st.sidebar.caption("No collectors or evaluations are launched from this interface.")

data_dir = os.environ.get("PROSPECT_DATA_DIR", "").strip()
if not data_dir:
    st.info("Set PROSPECT_DATA_DIR to your existing private Prospect data directory, then restart.")
    st.code('export PROSPECT_DATA_DIR="/path/to/private/prospect-data"', language="bash")
    st.stop()

try:
    with open_store(data_dir) as connection:
        if section == "Overview":
            counts, recent = overview(connection), runs(connection)
        elif section == "Run explorer":
            recent = runs(connection)
            if recent:
                # The selector displays friendly metadata, not internal IDs.
                run_labels = {
                    row["run_id"]: (
                        f'{row["started_at"]}  ·  {row["arm"]}  ·  '
                        f'{"finished" if row["finished_at"] else "unfinished"}'
                    )
                    for row in recent
                }
                run_id = st.selectbox(
                    "Inspect a collection run",
                    list(run_labels),
                    format_func=lambda value: run_labels[value],
                )
                selected_run = next(row for row in recent if row["run_id"] == run_id)
                run_poll_rows = polls(connection, limit=2000, run_id=run_id)
            else:
                selected_run, run_poll_rows = None, []
        elif section == "Sources":
            source_rows, poll_rows = sources(connection), polls(connection)
        else:
            all_rows = listings(connection)

    if section == "Overview":
        a, b, c, d = st.columns(4)
        a.metric("Registered public sources", counts["sources"])
        b.metric("Recorded polls", counts["polls"])
        c.metric("Distinct posting IDs seen", counts["unique_jobs"])
        d.metric("Incomplete / skipped polls", counts["incomplete_polls"])
        st.caption(f'Last collection poll: {counts["last_poll"] or "none recorded"}')
        st.subheader("Recent collection runs")
        if recent:
            st.dataframe(recent, hide_index=True)
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
                    "Filter poll status", status_options, default=status_options
                )
                filtered_polls = [
                    row for row in run_poll_rows if row["status"] in included
                ]
                st.dataframe(filtered_polls, hide_index=True)
                st.caption(
                    f"Showing {len(filtered_polls)} of {len(run_poll_rows)} recent recorded polls "
                    "(maximum 2,000). Counts may include repeated postings."
                )
            else:
                st.info("This run has no public discovery polls.")
            st.caption(
                "This page shows persisted polls, not boards that were planned but never "
                "dispatched or failures recorded only in pilot transport logs. "
                "It does not measure market recall."
            )

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

    else:
        st.subheader("Discovered job listings")
        if all_rows:
            needle = st.text_input("Filter title, employer, location, or source").casefold().strip()
            shown = [
                row for row in all_rows
                if not needle or any(
                    needle in str(row[field] or "").casefold()
                    for field in ("title", "employer", "location", "source")
                )
            ]
            st.caption(
                f"Showing {len(shown)} of {len(all_rows)} latest observed public postings "
                "(up to 2,000; not verified as currently open)."
            )
            st.dataframe(
                shown, hide_index=True,
                column_config={"url": st.column_config.LinkColumn("Public job link")}
            )
        else:
            st.info("No public job listings have been persisted yet.")
        st.caption("Repeated sightings of the same posting ID appear once.")

except FileNotFoundError:
    st.error("No prospect.sqlite found in PROSPECT_DATA_DIR.")
except (OSError, sqlite3.DatabaseError, ValueError):
    st.error("Unable to read the Prospect store. Check its permissions and schema version.")
    st.info("See dashboard/README.md for the expected database and setup.")
