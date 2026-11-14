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

section = st.sidebar.radio("View", ["Overview", "Sources", "Listings"])
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
