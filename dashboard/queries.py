"""Read-only, public-discovery projections from Prospect's local SQLite store.

No code in this module creates, migrates, or modifies a Prospect database.
Raw captures and evaluator/reference evidence are intentionally never queried.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from career_engine.store import DATABASE_NAME
from career_engine.store.privacy import Projection

REQUIRED_TABLES = {"schema_version", "channel", "run", "poll", "posting", "sighting"}
PUBLIC_DISCOVERY = "c.retention_class = 'public' AND c.evidence_role = 'discovery'"


def database_path(data_dir: str | Path) -> Path:
    if not str(data_dir).strip():
        raise ValueError("PROSPECT_DATA_DIR is required")
    return Path(data_dir).expanduser().resolve() / DATABASE_NAME


@contextmanager
def open_store(data_dir: str | Path):
    """Read one consistent snapshot of an existing store, including committed WAL data."""
    path = database_path(data_dir)
    if not path.is_file():
        raise FileNotFoundError("No prospect.sqlite found in PROSPECT_DATA_DIR.")
    uri = path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not REQUIRED_TABLES <= tables:
            raise ValueError("This file is not a supported Prospect collection store")
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if version != 1:
            raise ValueError(f"Unsupported Prospect store schema version: {version}")
        yield connection
    finally:
        connection.close()


def _rows(connection: sqlite3.Connection, sql: str, parameters=()) -> list[dict]:
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def overview(connection: sqlite3.Connection) -> dict:
    """Count persisted public discovery activity."""
    # Prospect timestamps are UTC with optional fractional seconds. Strip the
    # trailing Z before comparing so a whole second sorts before its fractions.
    return dict(connection.execute(f"""
        WITH public_polls AS (
          SELECT p.poll_id, p.channel_id, p.status, p.started_at
            FROM poll p JOIN channel c ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY}
        )
        SELECT
          (SELECT COUNT(*) FROM channel c WHERE {PUBLIC_DISCOVERY}) AS sources,
          COUNT(*) AS polls,
          (SELECT COUNT(DISTINCT s.posting_id) FROM sighting s
             JOIN public_polls p ON p.poll_id = s.poll_id AND p.channel_id = s.channel_id
          ) AS unique_jobs,
          COUNT(CASE WHEN status <> 'ok' THEN 1 END) AS incomplete_polls,
          MAX(rtrim(started_at, 'Z')) || 'Z' AS last_poll
        FROM public_polls
    """).fetchone())


def collection_totals(connection: sqlite3.Connection) -> dict:
    """Count committed public discovery records in the configured store."""
    return dict(connection.execute(f"""
        WITH public_polls AS (
          SELECT p.poll_id, p.channel_id, p.received
            FROM poll p JOIN channel c ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY}
        )
        SELECT
          (SELECT COALESCE(SUM(received), 0) FROM public_polls) AS received,
          COUNT(*) AS sightings,
          COUNT(DISTINCT s.posting_id) AS posting_ids
        FROM public_polls p
        JOIN sighting s ON s.poll_id = p.poll_id AND s.channel_id = p.channel_id
    """).fetchone())


def runs(connection: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = _rows(connection, f"""
        SELECT r.run_id, r.started_at, r.finished_at, r.arm, r.trigger,
               COUNT(*) AS polls, SUM(p.received) AS rows_received,
               SUM(p.unrepresented) AS unrepresented,
               COUNT(CASE WHEN p.status <> 'ok' THEN 1 END) AS incomplete_polls
          FROM run r
          JOIN poll p ON p.run_id = r.run_id
          JOIN channel c ON c.channel_id = p.channel_id
         WHERE {PUBLIC_DISCOVERY}
         GROUP BY r.run_id
         ORDER BY rtrim(r.started_at, 'Z') DESC, r.run_id DESC
         LIMIT ?
    """, (min(max(int(limit), 1), 1000),))
    privacy = Projection()
    for row in rows:
        for field in ("arm", "trigger"):
            row[field] = privacy.text(row[field])
    return rows


def sources(connection: sqlite3.Connection, limit: int = 1000) -> list[dict]:
    rows = _rows(connection, f"""
        WITH ranked_polls AS (
          SELECT p.channel_id, p.status, p.received, p.unrepresented, p.started_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY p.channel_id
                   ORDER BY rtrim(p.started_at, 'Z') DESC, p.poll_id DESC
                 ) AS recency
            FROM poll p JOIN channel c ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY}
        )
        SELECT c.platform, c.locator, c.verification_state,
               COUNT(p.channel_id) AS polls,
               COALESCE(SUM(p.received), 0) AS rows_received,
               COALESCE(SUM(p.unrepresented), 0) AS unrepresented,
               COUNT(CASE WHEN p.status <> 'ok' THEN 1 END) AS incomplete_polls,
               MAX(CASE WHEN p.recency = 1 THEN p.started_at END) AS last_polled,
               MAX(CASE WHEN p.recency = 1 THEN p.status END) AS latest_status
          FROM channel c
          LEFT JOIN ranked_polls p ON p.channel_id = c.channel_id
         WHERE {PUBLIC_DISCOVERY}
         GROUP BY c.channel_id
         ORDER BY rtrim(last_polled, 'Z') DESC, c.platform, c.locator, c.channel_id
         LIMIT ?
    """, (min(max(int(limit), 1), 5000),))
    privacy = Projection()
    for row in rows:
        row["platform"] = privacy.native(row["platform"])
        row["locator"] = privacy.url(row["locator"])
        row["verification_state"] = privacy.text(row["verification_state"])
        if row["latest_status"] is not None and privacy.unsafe(row["latest_status"]):
            row["latest_status"] = "skipped:redacted"
    return rows


def polls(connection: sqlite3.Connection, limit: int = 500, *,
          run_id: str | None = None) -> list[dict]:
    """Recent public collection polls, optionally restricted to one run.

    Results are bounded for display, not a count of all collection work.
    """
    rows = _rows(connection, f"""
        SELECT p.started_at, c.platform, c.locator, r.arm, p.status,
               p.received, p.unrepresented, p.declared_total
          FROM poll p
          JOIN run r ON r.run_id = p.run_id
          JOIN channel c ON c.channel_id = p.channel_id
         WHERE {PUBLIC_DISCOVERY}
           AND (? IS NULL OR r.run_id = ?)
         ORDER BY rtrim(p.started_at, 'Z') DESC, p.poll_id DESC
         LIMIT ?
    """, (run_id, run_id, min(max(int(limit), 1), 2000),))
    privacy = Projection()
    for row in rows:
        row["platform"] = privacy.native(row["platform"])
        row["locator"] = privacy.url(row["locator"])
        row["arm"] = privacy.text(row["arm"])
        if privacy.unsafe(row["status"]):
            row["status"] = "skipped:redacted"
    return rows


def listings(connection: sqlite3.Connection, limit: int = 2000) -> list[dict]:
    """Return the latest public-discovery sighting for each stored posting ID."""
    rows = _rows(connection, f"""
        WITH observations AS (
          SELECT s.posting_id, s.title, s.employer_name_raw AS employer,
                 s.location_raw AS location, s.url, s.canonical_url, c.platform AS source,
                 s.observed_at, s.posted_at, s.closing_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.posting_id
                   ORDER BY rtrim(s.observed_at, 'Z') DESC, s.sighting_id DESC
                 ) AS row_num
            FROM sighting s
            JOIN poll p ON p.poll_id = s.poll_id AND p.channel_id = s.channel_id
            JOIN channel c ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY}
        )
        SELECT posting_id, title, employer, location, source, observed_at,
               posted_at, closing_at, url, canonical_url
          FROM observations
         WHERE row_num = 1
         ORDER BY rtrim(observed_at, 'Z') DESC, employer, title, posting_id
         LIMIT ?
    """, (min(max(int(limit), 1), 5000),))
    privacy = Projection()
    for row in rows:
        observed_url, _ = privacy.posting_urls(row)
        row["url"] = None if observed_url == privacy.placeholder(row["posting_id"]) else observed_url
        del row["posting_id"], row["canonical_url"]
        row["source"] = privacy.native(row["source"])
        for field in ("title", "employer", "location"):
            row[field] = privacy.text(row[field])
    return rows


# The job browser queries the whole public discovery store rather than filtering
# only the first N rows returned by the diagnostic Listings projection.
_JOB_LATEST = f"""
    WITH latest AS (
      SELECT s.posting_id, s.title, s.employer_name_raw AS employer,
             s.location_raw AS location, s.url, s.canonical_url,
             c.platform AS source, s.observed_at, s.posted_at, s.closing_at,
             ROW_NUMBER() OVER (
               PARTITION BY s.posting_id
               ORDER BY rtrim(s.observed_at, 'Z') DESC, s.sighting_id DESC
             ) AS row_num
        FROM sighting s
        JOIN poll p ON p.poll_id = s.poll_id AND p.channel_id = s.channel_id
        JOIN channel c ON c.channel_id = p.channel_id
       WHERE {PUBLIC_DISCOVERY}
    )
"""

_SORT_JOBS = {
    "recent": "rtrim(observed_at, 'Z') DESC, employer COLLATE NOCASE, title COLLATE NOCASE, posting_id",
    "employer": "employer COLLATE NOCASE, title COLLATE NOCASE, rtrim(observed_at, 'Z') DESC, posting_id",
    "title": "title COLLATE NOCASE, employer COLLATE NOCASE, rtrim(observed_at, 'Z') DESC, posting_id",
}


def job_sources(connection: sqlite3.Connection) -> list[str]:
    """Sources of latest public discovery observations (not merely registered channels)."""
    results = connection.execute(
        _JOB_LATEST + "SELECT DISTINCT source FROM latest WHERE row_num = 1 ORDER BY source"
    ).fetchall()
    privacy = Projection()
    return [name for (value,) in results if (name := privacy.native(value)) is not None]


def browse_jobs(connection: sqlite3.Connection, *, search: str = "",
                location: str = "", source: str | None = None,
                sort: str = "recent", page: int = 1, page_size: int = 12) -> tuple[int, list[dict]]:
    """Search all latest public observations, count matches, and return one page.

    Page filtering/pagination happens in SQLite. Returned fields are projected
    through Prospect's existing shareable metadata policy before display.
    This does NOT verify vacancies are active or deduplicate across provider IDs.
    """
    if sort not in _SORT_JOBS:
        raise ValueError("Unsupported job sort order")
    if source is not None and (not isinstance(source, str) or Projection().native(source) is None):
        raise ValueError("Unsupported source filter")
    if not isinstance(page, int) or not 1 <= page <= 100000:
        raise ValueError("Unsupported page")
    if not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise ValueError("Unsupported page size")

    search = " ".join(search.split())[:120]
    location = " ".join(location.split())[:120]
    predicates = ["row_num = 1"]
    params = []
    if search:
        predicates.append("""
            instr(lower(coalesce(title, '') || ' ' || coalesce(employer, '') || ' ' ||
                        coalesce(location, '') || ' ' || coalesce(source, '')),
                  lower(?)) > 0
        """)
        params.append(search)
    if location:
        predicates.append("instr(lower(coalesce(location, '')), lower(?)) > 0")
        params.append(location)
    if source:
        predicates.append("source = ?")
        params.append(source)

    clause = " WHERE " + " AND ".join(predicates)
    total = connection.execute(
        _JOB_LATEST + "SELECT COUNT(*) FROM latest" + clause, params
    ).fetchone()[0]
    rows = _rows(connection,
        _JOB_LATEST +
        """SELECT posting_id, title, employer, location, source, observed_at,
                  posted_at, closing_at, url, canonical_url
             FROM latest""" + clause +
        " ORDER BY " + _SORT_JOBS[sort] + " LIMIT ? OFFSET ?",
        (*params, page_size, (page - 1) * page_size),
    )
    privacy = Projection()
    for row in rows:
        safe_url, _ = privacy.posting_urls(row)
        row["url"] = None if safe_url == privacy.placeholder(row["posting_id"]) else safe_url
        del row["posting_id"], row["canonical_url"]
        row["source"] = privacy.native(row["source"])
        for key in ("title", "employer", "location"):
            row[key] = privacy.text(row[key])
    return total, rows
