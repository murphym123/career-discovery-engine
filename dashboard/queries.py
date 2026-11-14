"""Read-only, public-discovery projections from Prospect's local SQLite store.

No code in this module creates, migrates, or modifies a Prospect database.
Raw captures and evaluator/reference evidence are intentionally never queried.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

REQUIRED_TABLES = {"schema_version", "channel", "run", "poll", "posting", "sighting"}
PUBLIC_DISCOVERY = "c.retention_class = 'public' AND c.evidence_role = 'discovery'"


def database_path(data_dir: str | Path) -> Path:
    if not str(data_dir).strip():
        raise ValueError("PROSPECT_DATA_DIR is required")
    return Path(data_dir).expanduser().resolve() / "prospect.sqlite"


@contextmanager
def open_store(data_dir: str | Path):
    """Open an existing store without creating a database or writing to WAL.

    SQLite's mode=ro handles a store that may be using WAL. query_only adds
    another guard against accidental writes. Never use immutable=1 on a live
    WAL database: it can silently hide newly committed records.
    """
    path = database_path(data_dir)
    if not path.is_file():
        raise FileNotFoundError(f"No Prospect database found at {path}")
    uri = "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
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
    """Counts are observed public discovery records, NOT market coverage."""
    return dict(connection.execute(f"""
        SELECT
          (SELECT COUNT(*) FROM channel c
           WHERE {PUBLIC_DISCOVERY}) AS sources,
          (SELECT COUNT(*) FROM poll p JOIN channel c
             ON c.channel_id = p.channel_id WHERE {PUBLIC_DISCOVERY}) AS polls,
          (SELECT COUNT(DISTINCT s.posting_id) FROM sighting s
             JOIN poll p ON p.poll_id = s.poll_id
             JOIN channel c ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY}) AS unique_jobs,
          (SELECT COUNT(*) FROM poll p JOIN channel c
             ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY} AND
             (p.status = 'partial' OR p.status = 'failed'
              OR p.status LIKE 'skipped:%')) AS incomplete_polls,
          (SELECT MAX(p.started_at) FROM poll p JOIN channel c
             ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY}) AS last_poll
    """).fetchone())


def runs(connection: sqlite3.Connection, limit: int = 100) -> list[dict]:
    return _rows(connection, f"""
        SELECT r.started_at, r.finished_at, r.arm, r.trigger,
               COUNT(*) AS polls, SUM(p.received) AS rows_received,
               SUM(CASE WHEN p.status <> 'ok' THEN 1 ELSE 0 END) AS incomplete_polls
          FROM run r
          JOIN poll p ON p.run_id = r.run_id
          JOIN channel c ON c.channel_id = p.channel_id
         WHERE {PUBLIC_DISCOVERY}
         GROUP BY r.run_id
         ORDER BY r.started_at DESC
         LIMIT ?
    """, (min(max(int(limit), 1), 1000),))


def sources(connection: sqlite3.Connection, limit: int = 1000) -> list[dict]:
    return _rows(connection, f"""
        SELECT c.platform, c.locator, c.verification_state,
               COUNT(p.poll_id) AS polls,
               COALESCE(SUM(p.received), 0) AS rows_received,
               COALESCE(SUM(p.unrepresented), 0) AS unrepresented,
               COALESCE(SUM(CASE WHEN p.status <> 'ok' THEN 1 ELSE 0 END), 0)
                 AS incomplete_polls,
               MAX(p.started_at) AS last_polled,
               (SELECT p2.status FROM poll p2
                 WHERE p2.channel_id = c.channel_id
                 ORDER BY p2.started_at DESC, p2.poll_id DESC LIMIT 1)
                 AS latest_status
          FROM channel c
          LEFT JOIN poll p ON p.channel_id = c.channel_id
         WHERE {PUBLIC_DISCOVERY}
         GROUP BY c.channel_id
         ORDER BY last_polled DESC, c.platform, c.locator
         LIMIT ?
    """, (min(max(int(limit), 1), 5000),))


def polls(connection: sqlite3.Connection, limit: int = 500) -> list[dict]:
    return _rows(connection, f"""
        SELECT p.started_at, c.platform, c.locator, r.arm, p.status,
               p.received, p.unrepresented, p.declared_total
          FROM poll p
          JOIN run r ON r.run_id = p.run_id
          JOIN channel c ON c.channel_id = p.channel_id
         WHERE {PUBLIC_DISCOVERY}
         ORDER BY p.started_at DESC, p.poll_id DESC
         LIMIT ?
    """, (min(max(int(limit), 1), 2000),))


def listings(connection: sqlite3.Connection, limit: int = 2000) -> list[dict]:
    """Latest public-discovery sighting per posting, not confirmed open jobs."""
    return _rows(connection, f"""
        WITH observations AS (
          SELECT s.posting_id, s.title, s.employer_name_raw AS employer,
                 s.location_raw AS location, s.url, c.platform AS source,
                 s.observed_at, s.posted_at, s.closing_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.posting_id
                   ORDER BY s.observed_at DESC, s.sighting_id DESC
                 ) AS row_num
            FROM sighting s
            JOIN poll p ON p.poll_id = s.poll_id
            JOIN channel c ON c.channel_id = p.channel_id
           WHERE {PUBLIC_DISCOVERY}
        )
        SELECT title, employer, location, source, observed_at, posted_at,
               closing_at, url
          FROM observations
         WHERE row_num = 1
         ORDER BY observed_at DESC, employer, title
         LIMIT ?
    """, (min(max(int(limit), 1), 5000),))
