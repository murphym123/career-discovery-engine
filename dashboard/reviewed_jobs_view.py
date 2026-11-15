"""Streamlit view for all privately assessed jobs, including negative decisions."""
import streamlit as st

from dashboard.browse import markdown_text as _markdown
from dashboard.reviewed_jobs import (
    FIT_LABELS, ReviewResultsError, filter_reviews, load_review_results,
    review_results_path,
)


@st.cache_data(ttl=60, max_entries=4, show_spinner=False)
def read_results(path, revision):
    return load_review_results(path)


def revision(path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except (OSError, AttributeError):
        return None


def render_reviewed_jobs():
    st.header("Reviewed Jobs")
    st.caption("All saved full-description assessments, including possible and poor fits. "
               "These classifications are provisional, not employer decisions or live-availability checks.")
    path = review_results_path()
    try:
        document = read_results(path, revision(path))
    except (ReviewResultsError, OSError) as error:
        st.error(f"Cannot load private review results: {error}")
        return
    if document is None or not document["records"]:
        st.info("No reviewed jobs yet. Set PROSPECT_REVIEW_RESULTS to the private deep_review.json path.")
        return

    rows = document["records"]
    counts = {c: sum(r["fit_category"] == c for r in rows) for c in FIT_LABELS}
    st.caption(" · ".join(f"{FIT_LABELS[c]}: {counts[c]}" for c in FIT_LABELS))
    one, two, three = st.columns([2, 1, 2])
    search = one.text_input("Search titles or employers", key="reviewed_search")
    category = two.selectbox("Fit category", ["all", *FIT_LABELS], format_func=lambda c: FIT_LABELS.get(c, "All assessments"), key="reviewed_category")
    employers = ["All employers", *sorted({r["employer"] for r in rows}, key=str.casefold)]
    employer = three.selectbox("Employer", employers, key="reviewed_employer")
    selected = filter_reviews(rows, category=category, search=search, employer=employer)
    st.markdown(f"**Showing {len(selected)} of {len(rows)} reviewed jobs**")
    if not selected:
        st.info("No reviewed jobs match these filters.")
        return

    for r in selected:
        with st.container(border=True):
            head = f"**{_markdown(r['title'])}** — {_markdown(r['employer'])}"
            st.markdown(head)
            st.caption(f"{FIT_LABELS[r['fit_category']]} · {_markdown(r['location'] or 'Location unconfirmed')}"
                       + (" · Also on curated Shortlist" if r["shortlisted"] else ""))
            if r["needs_further_evidence"]:
                st.warning("Needs evidence. This is not a determination that you are unqualified.")
            if r["availability_status"] == "verified_current":
                st.caption(f"Posting checked: {r['availability_checked_at']}")
            else:
                st.caption("Availability not independently verified for this review record")
            if r["posting_evidence"]:
                st.markdown("**Posting evidence:** " + _markdown(r["posting_evidence"]))
            if r["applicant_evidence"]:
                st.markdown("**Relevant experience:** " + _markdown(r["applicant_evidence"]))
            if r["barriers_or_unknowns"]:
                st.markdown("**Concerns / missing evidence:** " + _markdown(r["barriers_or_unknowns"]))
            if r["official_url"]:
                st.link_button("Original posting (verify current status)", r["official_url"])
