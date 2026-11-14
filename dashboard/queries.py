"""Read-only, public-discovery projections from Prospect's local SQLite store.

No code in this module creates, migrates, or modifies a Prospect database.
Raw captures and evaluator/reference evidence are intentionally never queried.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from career_engine.store import DATABASE_NAME, parse_timestamp
from career_engine.store.privacy import REDACTED, Projection

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

# Rank only public discovery sightings before applying any filter. Filtering
# before ranking could return obsolete observations of the same posting ID.
_JOB_LATEST = f"""
    WITH ranked AS (
      SELECT s.sighting_id, s.posting_id, c.platform,
             ROW_NUMBER() OVER (
               PARTITION BY s.posting_id
               ORDER BY rtrim(s.observed_at, 'Z') DESC, s.sighting_id DESC
             ) AS recency
        FROM sighting s
        JOIN poll p ON p.poll_id = s.poll_id AND p.channel_id = s.channel_id
        JOIN channel c ON c.channel_id = p.channel_id
       WHERE {PUBLIC_DISCOVERY}
    )
"""

_SORT_JOBS = {
    "recent": "rtrim(observed_at, 'Z') DESC NULLS LAST, employer_key NULLS LAST, title_key NULLS LAST, posting_id",
    "employer": "employer_key NULLS LAST, title_key NULLS LAST, rtrim(observed_at, 'Z') DESC NULLS LAST, posting_id",
    "title": "title_key NULLS LAST, employer_key NULLS LAST, rtrim(observed_at, 'Z') DESC NULLS LAST, posting_id",
}
_UTC_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")


def job_sources(connection: sqlite3.Connection) -> list[str]:
    """Offer only shareable platforms of latest public discovery sightings."""
    privacy = Projection()
    rows = connection.execute(
        _JOB_LATEST + "SELECT DISTINCT platform FROM ranked WHERE recency = 1 ORDER BY platform"
    )
    return [safe for (platform,) in rows if (safe := privacy.native(platform)) is not None]


def _job_date(value, privacy):
    if not value or not _UTC_DATE.fullmatch(value) or privacy.unsafe(value):
        return None
    try:
        parse_timestamp(value)
    except ValueError:
        return None
    return value


def browse_jobs(connection: sqlite3.Connection, *, search: str = "", employer: str = "",
                location: str = "", source: str | None = None, sort: str = "recent",
                page: int = 1, page_size: int = 12) -> tuple[int, int, list[dict]]:
    """Return the matching count, effective page, and one page of safe metadata."""
    if sort not in _SORT_JOBS:
        raise ValueError("Unsupported job sort order")
    if type(page) is not int or not 1 <= page <= 2**63 - 1:
        raise ValueError("Unsupported page")
    if type(page_size) is not int or not 1 <= page_size <= 50:
        raise ValueError("Unsupported page size")
    privacy = Projection()
    if source is not None and (not isinstance(source, str) or privacy.native(source) is None):
        raise ValueError("Unsupported source filter")

    @lru_cache(maxsize=4096)
    def public_text(value):
        safe = privacy.text(value).strip()
        return safe if safe and safe != REDACTED else None

    connection.create_function("job_text", 1, public_text, deterministic=True)
    connection.create_function("job_platform", 1, privacy.native, deterministic=True)
    connection.create_function("job_fold", 1, lambda value: value.casefold() if value else None,
                               deterministic=True)
    connection.create_function("job_observed", 1, lambda value: _job_date(value, privacy),
                               deterministic=True)

    predicates = []
    params = {"page": page, "size": page_size}
    search = " ".join(search.split())[:120]
    for index, term in enumerate(search.casefold().split()):
        key = f"word{index}"
        params[key] = term
        predicates.append(f"""
            (instr(title_key, :{key}) > 0 OR instr(employer_key, :{key}) > 0
             OR instr(location_key, :{key}) > 0)
        """)
    for field, value in (("employer", employer), ("location", location)):
        value = value.strip()[:120]
        if value:
            params[field] = value.casefold()
            predicates.append(f"instr({field}_key, :{field}) > 0")
    if source is not None:
        params["source"] = source
        predicates.append("source = :source")
    where = " AND ".join(predicates) or "1"
    order = _SORT_JOBS[sort]
    # All matching and ordering uses projected values. SQLite holds only the
    # fields needed to rank/count. Read posting URLs after selecting the page.
    try:
        result = _rows(connection, _JOB_LATEST + f"""
            , display AS MATERIALIZED (
                SELECT r.sighting_id, r.posting_id,
                       job_text(s.title) AS title, job_text(s.employer_name_raw) AS employer,
                       job_text(s.location_raw) AS location, job_platform(r.platform) AS source,
                       job_observed(s.observed_at) AS observed_at
                  FROM ranked r JOIN sighting s ON s.sighting_id = r.sighting_id
                 WHERE r.recency = 1
            ), searchable AS (
                SELECT *, job_fold(title) AS title_key,
                       job_fold(employer) AS employer_key,
                       job_fold(location) AS location_key
                  FROM display
            ), matched AS MATERIALIZED (
                SELECT * FROM searchable WHERE {where}
            ), tally AS (
                SELECT COUNT(*) AS total FROM matched
            ), bounds AS (
                SELECT total, min(:page, max(1, (total + :size - 1) / :size)) AS page
                  FROM tally
            ), selected AS (
                SELECT * FROM matched ORDER BY {order}
                 LIMIT :size OFFSET (SELECT (page - 1) * :size FROM bounds)
            )
            SELECT bounds.*, selected.*, details.url, details.canonical_url,
                   details.posted_at, details.closing_at, source_channel.locator
              FROM bounds LEFT JOIN selected ON 1
              LEFT JOIN (
                   SELECT sighting_id, channel_id, url, canonical_url, posted_at, closing_at FROM sighting
              ) details ON details.sighting_id = selected.sighting_id
              LEFT JOIN channel source_channel ON source_channel.channel_id = details.channel_id
             ORDER BY {order}
        """, params)
    finally:
        for name in ("job_text", "job_platform", "job_fold", "job_observed"):
            connection.create_function(name, 1, None)

    total, effective_page = result[0]["total"], result[0]["page"]
    jobs = []
    for row in result:
        if row["sighting_id"] is None:
            continue
        observed_url, _ = privacy.posting_urls(row)
        jobs.append({
            "title": row["title"], "employer": row["employer"], "location": row["location"],
            "source": row["source"], "source_url": privacy.url(row["locator"]),
            "observed_at": row["observed_at"],
            "posted_at": _job_date(row["posted_at"], privacy),
            "closing_at": _job_date(row["closing_at"], privacy),
            "url": observed_url if observed_url != privacy.placeholder(row["posting_id"]) else None,
        })
    return total, effective_page, jobs
