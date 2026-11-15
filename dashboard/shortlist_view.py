"""Streamlit rendering for the curated Shortlist (data handling lives in shortlist.py)."""
import streamlit as st

from dashboard.browse import markdown_text as browse_markdown
from dashboard.shortlist import (
    AVAILABILITY_LABELS, FEEDBACK_LABELS, PRIORITY_LABELS, ShortlistError,
    feedback_path, filter_jobs, load_feedback, load_shortlist, save_feedback, shortlist_path,
)

NO_FEEDBACK = "none"


def markdown_text(value):
    # Also escape "$" so salary ranges are not rendered as LaTeX.
    return browse_markdown(value).replace("$", "\\$")


@st.cache_data(max_entries=4, ttl=60, show_spinner=False)
def read_shortlist(path, revision):
    # revision (mtime, size) invalidates the cache when the file is replaced.
    return load_shortlist(path)


def file_revision(path):
    try:
        info = path.stat()
    except (OSError, AttributeError):
        return None
    return info.st_mtime_ns, info.st_size


def feedback_label(value):
    return "No reaction yet" if value == NO_FEEDBACK else FEEDBACK_LABELS[value]


def record_feedback(target, review_id):
    choice = st.session_state[f"feedback_{review_id}"]
    decision = None if choice == NO_FEEDBACK else choice
    st.session_state.shortlist_feedback[review_id] = decision
    if target is None:
        return
    try:
        save_feedback(target, review_id, decision)
    except OSError:
        st.session_state.shortlist_feedback_error = True


def render_card(job, saved_feedback, target):
    with st.container(border=True):
        st.markdown("### " + markdown_text(job["title"] or "Job title unavailable"))
        employer = markdown_text(job["employer"] or "Employer unavailable")
        st.markdown(f"**{employer}**")
        place = [job["location"] or "Location not stated",
                 job["work_arrangement"] or "Working arrangement not stated",
                 job["compensation_text"] or "Salary not stated"]
        st.caption(" · ".join(markdown_text(part) for part in place))

        priority = PRIORITY_LABELS[job["review_priority"]]
        if job["availability_status"] == "verified_current":
            st.success(f'Verified current on {job["availability_checked"]} · Priority: {priority}')
        elif job["availability_status"] == "no_longer_open":
            checked = f' (checked {job["availability_checked"]})' if job["availability_checked"] else ""
            st.error(f"No longer open{checked} · Priority: {priority}")
        else:
            st.warning(f"NOT YET VERIFIED. Confirm the posting is open · Priority: {priority}")

        fit, gaps = st.columns(2)
        fit.markdown("**Why it fits**")
        fit.markdown("\n".join(f"- {markdown_text(reason)}" for reason in job["fit_reasons"])
                     or "_No fit evidence supplied._")
        gaps.markdown("**Check before applying**")
        gaps.markdown("\n".join(f"- {markdown_text(gap)}" for gap in job["gaps_to_verify"])
                      or "_No open questions supplied._")

        posting, apply, reaction = st.columns([1, 1, 2])
        if job["official_url"]:
            posting.link_button("View original posting", job["official_url"])
            apply.link_button("Apply on employer site", job["application_url"], type="primary")
        else:
            posting.caption("Original posting link unavailable (missing or unsafe)")
        key = f'feedback_{job["review_id"]}'
        st.session_state.setdefault(key, saved_feedback.get(job["review_id"]) or NO_FEEDBACK)
        reaction.radio("Your take", [NO_FEEDBACK, *FEEDBACK_LABELS], format_func=feedback_label,
                       horizontal=True, key=key, on_change=record_feedback,
                       args=(target, job["review_id"]))


def render_shortlist():
    st.header("Shortlist")
    path = shortlist_path()
    try:
        shortlist = read_shortlist(path, file_revision(path))
    except ShortlistError as error:
        st.error(f"The shortlist file cannot be shown: {error}.")
        return
    except OSError:
        st.error("The shortlist file could not be read. Check its permissions.")
        return
    if shortlist is None or not shortlist["jobs"]:
        st.info(
            "**No shortlist yet.** Nothing has been curated for review. "
            "Prospect shows no placeholder jobs here."
        )
        st.caption("Place `shortlist.json` in PROSPECT_DATA_DIR or set PROSPECT_SHORTLIST to its path.")
        return

    target = None if shortlist["demo"] else feedback_path(path)
    if "shortlist_feedback" not in st.session_state:
        st.session_state.shortlist_feedback = load_feedback(target)
    saved = {key: (value["decision"] if isinstance(value, dict) else value)
             for key, value in st.session_state.shortlist_feedback.items()}

    search, availability, priority = st.columns([2, 1, 1])
    search.text_input("Search shortlist", max_chars=120, key="shortlist_search",
                      placeholder="Title, employer, or location")
    availability.multiselect("Verification", list(AVAILABILITY_LABELS),
                             format_func=AVAILABILITY_LABELS.get, key="shortlist_availability",
                             placeholder="Any status")
    priority.multiselect("Priority", list(PRIORITY_LABELS), format_func=PRIORITY_LABELS.get,
                         key="shortlist_priority", placeholder="Any priority")
    jobs = filter_jobs(
        shortlist["jobs"], search=st.session_state.shortlist_search,
        availability=st.session_state.shortlist_availability,
        priority=st.session_state.shortlist_priority,
    )
    total = len(shortlist["jobs"])
    verified = sum(job["availability_status"] == "verified_current" for job in shortlist["jobs"])
    generated = f' · Generated {shortlist["generated_at"]}' if shortlist["generated_at"] else ""
    st.markdown(f"**Showing {len(jobs)} of {total} curated jobs** · {verified} verified current{generated}")
    if shortlist["demo"]:
        # Below the filters so nothing sits between the title and the search bar.
        st.warning("DEMO SHORTLIST: synthetic jobs, not real openings.")
    if target is None:
        st.caption("Your reactions stay in this browser session only and are not saved.")
    else:
        st.caption("Your reactions are saved only to a local feedback file beside the shortlist.")
    if st.session_state.pop("shortlist_feedback_error", False):
        st.warning("Your last reaction could not be saved to the local feedback file.")
    if not jobs:
        st.info("No shortlisted jobs match these filters.")
        return
    for job in jobs:
        render_card(job, saved, target)
