"""Job-first Streamlit view for the local, read-only dashboard."""
import re

import streamlit as st

from career_engine.store import parse_timestamp
from dashboard.queries import browse_jobs, job_sources, open_store

PAGE_SIZE = 10
DEFAULTS = dict(search="", employer="", location="", source=None, sort="recent")
SORT_LABELS = {"recent": "Recently observed", "employer": "Employer A–Z", "title": "Job title A–Z"}
PLATFORM_LABELS = {
    "greenhouse": "Greenhouse", "lever": "Lever", "lever_eu": "Lever EU",
    "jazzhr": "JazzHR", "usajobs": "USAJOBS", "icims": "iCIMS", "ashby": "Ashby",
}


@st.cache_data(max_entries=8, ttl=60, show_spinner=False)
def read_sources(data_dir, revision):
    with open_store(data_dir) as connection:
        return job_sources(connection)


@st.cache_data(max_entries=24, ttl=60, show_spinner=False)
def read_jobs(data_dir, revision, filters, page):
    with open_store(data_dir) as connection:
        return browse_jobs(connection, **filters, page=page, page_size=PAGE_SIZE)


def change_filter(name):
    st.session_state.job_filters[name] = st.session_state[f"job_{name}"]
    st.session_state.job_page = 1


def reset_filters():
    st.session_state.job_filters = DEFAULTS.copy()
    st.session_state.job_page = 1
    for name, value in DEFAULTS.items():
        st.session_state[f"job_{name}"] = value


def set_page(page):
    st.session_state.job_page = page


def source_label(value):
    if value is None:
        return "All platforms"
    return PLATFORM_LABELS.get(value, value)


def markdown_text(value):
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~])", r"\\\1", value)


def date_label(value, *, show_time=False):
    if value is None:
        return "Unavailable"
    date = parse_timestamp(value)
    day = f"{date:%b} {date.day}, {date.year}"
    return f"{day} at {date:%H:%M} UTC" if show_time else day


def render_job(job):
    with st.container(border=True):
        st.markdown("### " + markdown_text(job["title"] or "Job title unavailable"))
        employer = markdown_text(job["employer"] or "Employer unavailable")
        location = markdown_text(job["location"] or "Location unavailable")
        st.markdown(f"**{employer}** · {location}")
        source = source_label(job["source"]) if job["source"] else "Unavailable"
        source = markdown_text(source)
        if job["source_url"]:
            source_url = job["source_url"].replace(" ", "%20")
            source = f"[{source}](<{source_url}>)"
        timing = [f"Source {source}", f"Last observed {date_label(job['observed_at'], show_time=True)}"]
        if job["posted_at"]:
            timing.append(f"Posted {date_label(job['posted_at'])}")
        if job["closing_at"]:
            timing.append(f"Closing date {date_label(job['closing_at'])}")
        st.caption(" · ".join(timing))
        if job["url"]:
            url = job["url"].replace(" ", "%20")
            st.markdown(f"[View original posting](<{url}>)")
        else:
            st.caption("Original posting link unavailable")


def page_controls(page, pages, position):
    previous, label, following = st.columns([1, 2, 1])
    previous.button("Previous page", disabled=page == 1, on_click=set_page,
                    args=(page - 1,), key=f"previous_{position}")
    label.caption(f"Page {page:,} of {pages:,}")
    following.button("Next page", disabled=page == pages, on_click=set_page,
                     args=(page + 1,), key=f"next_{position}")


def render_browse(data_dir, revision):
    st.header("Browse Jobs")
    st.caption(
        "Stored observations. Jobs may have closed since they were observed, "
        "and different posting IDs can refer to the same vacancy."
    )
    platforms = read_sources(data_dir, revision)
    if "job_filters" not in st.session_state:
        reset_filters()
    filters = st.session_state.job_filters
    if filters["source"] is not None and filters["source"] not in platforms:
        st.session_state.job_page = 1
        filters["source"] = st.session_state.job_source = None
    # Streamlit clears widget state when the user leaves this view.
    for name, value in filters.items():
        st.session_state.setdefault(f"job_{name}", value)

    st.text_input("Search jobs", placeholder="e.g. research assistant Washington", max_chars=120,
                  key="job_search", on_change=change_filter, args=("search",))
    employer, location = st.columns(2)
    employer.text_input("Employer", placeholder="Any employer", max_chars=120,
                        key="job_employer", on_change=change_filter, args=("employer",))
    location.text_input("Location", placeholder="Any location", max_chars=120,
                        key="job_location", on_change=change_filter, args=("location",))
    source, sort = st.columns(2)
    source.selectbox("Source platform", [None, *platforms], format_func=source_label,
                     placeholder="All platforms", key="job_source", on_change=change_filter, args=("source",))
    sort.selectbox("Sort by", list(SORT_LABELS), format_func=SORT_LABELS.get,
                   key="job_sort", on_change=change_filter, args=("sort",))
    reset, refresh = st.columns(2)
    reset.button("Reset filters", on_click=reset_filters)
    refresh.button("Refresh results", help="Pick up newly committed observations.")

    total, page, jobs = read_jobs(data_dir, revision, dict(filters), st.session_state.job_page)
    st.session_state.job_page = page
    if not jobs:
        st.markdown("**0 matching posting IDs**")
        st.info("No postings match these filters. Try a shorter search or reset the filters."
                if any(filters[key] for key in ("search", "employer", "location", "source"))
                else "No public discovery postings have been stored yet.")
        return

    first = (page - 1) * PAGE_SIZE + 1
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    noun = "posting ID" if total == 1 else "posting IDs"
    st.markdown(f"**{total:,} matching {noun}** · Showing {first:,}–{first + len(jobs) - 1:,}")
    if pages > 1:
        page_controls(page, pages, "top")
    for job in jobs:
        render_job(job)
    if pages > 1:
        page_controls(page, pages, "bottom")
