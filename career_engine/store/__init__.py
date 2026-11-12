"""Local SQLite store for collection history and versioned classification evidence."""
import os
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from career_engine.store.blobs import BlobStore, digest, managed_path, outside_git

SCHEMA_VERSION = 1
DATABASE_NAME = "prospect.sqlite"


def timestamp(value=None):
    """Normalize a datetime or timestamp to UTC, preserving subsecond precision."""
    value = datetime.now(timezone.utc) if value is None else value
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.utcoffset() is None:
        raise ValueError("Timestamp must specify a timezone")
    utc = value.astimezone(timezone.utc).isoformat().removesuffix("+00:00")
    return (utc.rstrip("0") if "." in utc else utc) + "Z"


def git_sha(value=None):
    if value is None:
        value = os.environ.get("PROSPECT_CODE_SHA")
    if value is None:
        try:
            value = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            raise ValueError("Set PROSPECT_CODE_SHA to the Prospect source commit") from None
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("Expected a full lowercase Git commit SHA")
    return value


class Store:
    def __init__(self, path, *, clock=timestamp, create=True):
        self.path = outside_git(path)
        if not create and not self.path.is_file():
            raise FileNotFoundError("Prospect database does not exist")
        self.blobs = BlobStore(self.path.parent)
        self.clock = clock
        self.connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            tables = {row[0] for row in self.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )}
            if not tables and create:
                self.connection.executescript(Path(__file__).with_name("schema.sql").read_text())
            elif "schema_version" not in tables:
                raise ValueError("Database has no Prospect schema version")
            self.schema_version = self.connection.execute("SELECT max(version) FROM schema_version").fetchone()[0]
            if self.schema_version != SCHEMA_VERSION:
                raise ValueError("Unsupported Prospect store schema version")
            if tables and not {"poll_accounting", "poll_inventory"} <= tables:
                # Add the accounting tables to existing M1a stores in place.
                self.connection.executescript(Path(__file__).with_name("schema.sql").read_text())
            self.connection.execute("PRAGMA journal_mode = WAL")
        except BaseException:
            self.connection.close()
            raise

    @classmethod
    def in_data_dir(cls, data_dir, **options):
        return cls(managed_path(outside_git(data_dir), DATABASE_NAME), **options)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.connection.close()

    def now(self):
        return timestamp(self.clock())

    @contextmanager
    def transaction(self, *, write=True):
        db = self.connection
        nested = db.in_transaction
        db.execute("SAVEPOINT prospect_write" if nested else ("BEGIN IMMEDIATE" if write else "BEGIN"))
        try:
            yield db
            db.execute("RELEASE prospect_write" if nested else "COMMIT")
        except BaseException:
            if nested:
                db.execute("ROLLBACK TO prospect_write")
                db.execute("RELEASE prospect_write")
            else:
                db.rollback()
            raise

    def begin_run(self, *, code_sha, config_sha, trigger="manual", arm="baseline",
                  started_at=None, run_id=None):
        run_id = run_id or uuid4().hex
        self.connection.execute(
            "INSERT INTO run (run_id, started_at, code_sha, config_sha, trigger, arm) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, timestamp(started_at) if started_at is not None else self.now(),
             git_sha(code_sha), digest(config_sha), trigger, arm),
        )
        return run_id

    def end_run(self, run_id, *, finished_at=None):
        finished_at = timestamp(finished_at) if finished_at is not None else self.now()
        if self.connection.in_transaction:
            raise RuntimeError("Finish the write transaction before ending a run")
        with self.transaction() as db:
            row = db.execute("SELECT started_at, finished_at FROM run WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown run")
            if finished_at[:-1] < row["started_at"][:-1]:
                raise ValueError("Run finish precedes its start")
            if row["finished_at"] is None:
                db.execute("UPDATE run SET finished_at = ? WHERE run_id = ?", (finished_at, run_id))
        self.backup()
        self.blobs.purge("public")

    def backup(self):
        directory = managed_path(self.path.parent, "backups")
        directory.mkdir(mode=0o700, exist_ok=True)
        # Serialize publication and rotation across Store connections.
        with self.blobs.locked(write=True):
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as staging:
                temporary = Path(staging.name)
            target = directory / f"{timestamp().replace(':', '')}_{uuid4().hex}.sqlite"
            try:
                with closing(sqlite3.connect(temporary)) as copy:
                    self.connection.backup(copy)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            backups = sorted(
                (path for path in directory.glob("*_*.sqlite")
                 if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{6}(?:\.\d+)?Z_[0-9a-f]{32}\.sqlite", path.name)),
                key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True,
            )
            months = set()
            for index, backup in enumerate(backups):
                month = backup.name[:7]
                if index >= 14 and month in months:
                    backup.unlink()
                months.add(month)
        return target

    def add_employer(self, canonical_name, status, origin, *, employer_id=None, aliases=()):
        employer_id = employer_id or uuid4().hex
        with self.transaction() as db:
            db.execute("INSERT INTO employer VALUES (?, ?, ?, ?, NULL)",
                       (employer_id, canonical_name, status, origin))
            db.executemany("INSERT INTO employer_alias VALUES (?, ?, ?)",
                           ((employer_id, alias["kind"], alias["value"]) for alias in aliases))
        return employer_id

    def merge_employer(self, employer_id, successor_id):
        with self.transaction() as db:
            cycle = db.execute("""
                WITH RECURSIVE chain(id) AS (
                    VALUES (?) UNION SELECT merged_into FROM employer JOIN chain ON employer_id = chain.id
                    WHERE merged_into IS NOT NULL
                )
                SELECT 1 FROM chain WHERE id = ?
            """, (successor_id, employer_id)).fetchone()
            if cycle:
                raise ValueError("An employer merge cannot create a cycle")
            changed = db.execute(
                "UPDATE employer SET status = 'merged', merged_into = ? WHERE employer_id = ? AND status <> 'merged'",
                (successor_id, employer_id),
            )
            if changed.rowcount != 1:
                raise ValueError("Expected an existing employer that has not already been merged")

    def ensure_channel(self, *, kind, platform, locator, retention_class, evidence_role,
                       verification_state, channel_key=None, employer_id=None, channel_id=None):
        channel_id = channel_id or uuid4().hex
        with self.transaction() as db:
            db.execute("""
                INSERT INTO channel VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_key) DO NOTHING
            """, (channel_id, channel_key, employer_id, kind, platform, locator, retention_class,
                  evidence_role, verification_state))
            if channel_key is not None:
                row = db.execute(
                    "SELECT channel_id, retention_class FROM channel WHERE channel_key = ?", (channel_key,),
                ).fetchone()
                if row["retention_class"] != retention_class:
                    raise ValueError("Channel retention class conflicts with its existing record")
                channel_id = row["channel_id"]
        return channel_id

    def register_capture(self, sha, byte_count, *, captured_at=None):
        self.connection.execute("""
            INSERT INTO raw_capture VALUES (?, 'public', ?, ?)
            ON CONFLICT(raw_sha) DO NOTHING
        """, (digest(sha), timestamp(captured_at) if captured_at is not None else self.now(), byte_count))

    def record_poll(self, run_id, channel_id, *, status, received, declared_total=None,
                    raw_sha=None, started_at=None, poll_id=None, captures=(), unrepresented=0,
                    inventory=()):
        if not re.fullmatch(r"(?:ok|partial|failed|skipped:[a-z][a-z0-9_]{0,39})", status):
            raise ValueError("Invalid poll status or skip reason")
        if type(unrepresented) is not int or not 0 <= unrepresented <= received:
            raise ValueError("Unrepresented count must be between zero and received")
        if unrepresented and status not in ("partial", "failed"):
            raise ValueError("A poll with unrepresented records cannot be complete")
        inventory = tuple(inventory)
        withheld_inventory_responses = sum(state == "secret_echo" for state, _, _ in inventory)
        capture_withheld = "secret_echo" if withheld_inventory_responses else None
        primary = next((sha for state, sha, _ in inventory if state == "retained"), None)
        if inventory and raw_sha != primary:
            raise ValueError("Primary poll capture must be the first retained inventory response")
        poll_id = poll_id or uuid4().hex
        started_at = timestamp(started_at) if started_at is not None else self.now()
        captures = tuple(captures)
        with self.transaction() as db:
            channel = db.execute("SELECT retention_class FROM channel WHERE channel_id = ?", (channel_id,)).fetchone()
            run = db.execute("SELECT started_at, finished_at FROM run WHERE run_id = ?", (run_id,)).fetchone()
            if channel is None or run is None or run["finished_at"] is not None or started_at[:-1] < run["started_at"][:-1]:
                raise ValueError("Poll requires a known channel and an open run")
            retention = channel["retention_class"]
            if retention != "public" and (raw_sha is not None or captures or inventory):
                raise ValueError("Poll capture requires a public channel")
            if retention in ("licensed", "restricted") and (received or status != f"skipped:{retention}"):
                raise ValueError("This channel can only record a skip")
            db.execute("INSERT INTO poll VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (poll_id, run_id, channel_id, started_at, status, declared_total, received, unrepresented, raw_sha))
            if unrepresented or withheld_inventory_responses:
                db.execute("INSERT INTO poll_accounting VALUES (?, ?, ?)",
                           (poll_id, withheld_inventory_responses, capture_withheld))
            db.executemany("INSERT INTO poll_inventory VALUES (?, ?, ?, ?, ?)",
                           ((poll_id, i, *page) for i, page in enumerate(inventory)))
            db.executemany("INSERT INTO poll_capture VALUES (?, ?, ?, ?)",
                           ((poll_id, i, sha, role) for i, (sha, role) in enumerate(captures)))
        return poll_id

    def record_sighting(self, poll_id, *, identity_key, url, canonical_url, title,
                        employer_name_raw, location_raw, native_id=None, employer_id=None,
                        posted_at=None, closing_at=None, observed_at=None, description_sha=None,
                        raw_sha=None, posting_id=None, sighting_id=None):
        if not identity_key:
            raise ValueError("A posting requires an identity key")
        sighting_id = sighting_id or uuid4().hex
        observed_at = timestamp(observed_at) if observed_at is not None else self.now()
        with self.transaction() as db:
            poll = db.execute("""
                SELECT poll.channel_id, poll.started_at, poll.status, run.finished_at,
                       channel.retention_class, channel.employer_id
                FROM poll JOIN run USING (run_id) JOIN channel USING (channel_id) WHERE poll_id = ?
            """, (poll_id,)).fetchone()
            if (poll is None or poll["finished_at"] is not None or poll["status"].startswith("skipped:")
                    or observed_at[:-1] < poll["started_at"][:-1]):
                raise ValueError("Sighting requires an earlier, attempted poll")
            if poll["retention_class"] != "public" and (raw_sha is not None or description_sha is not None):
                raise ValueError("Capture and description digests require a public channel")
            row = db.execute("""
                INSERT INTO posting (posting_id, employer_id, identity_key, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    employer_id = coalesce(posting.employer_id, excluded.employer_id),
                    first_seen = CASE WHEN rtrim(excluded.first_seen, 'Z') < rtrim(posting.first_seen, 'Z')
                                      THEN excluded.first_seen ELSE posting.first_seen END,
                    last_seen = CASE WHEN rtrim(excluded.last_seen, 'Z') > rtrim(posting.last_seen, 'Z')
                                     THEN excluded.last_seen ELSE posting.last_seen END
                RETURNING posting_id
            """, (posting_id or uuid4().hex, employer_id or poll["employer_id"], identity_key, observed_at, observed_at)).fetchone()
            if posting_id is not None and row["posting_id"] != posting_id:
                raise ValueError("Posting identity already has a different stable ID")
            db.execute("""
                INSERT INTO sighting VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sighting_id, row["posting_id"], poll_id, poll["channel_id"], native_id, url, canonical_url,
                  title, employer_name_raw, location_raw,
                  timestamp(posted_at) if posted_at is not None else None,
                  timestamp(closing_at) if closing_at is not None else None,
                  observed_at, digest(description_sha) if description_sha is not None else None, raw_sha))
        return row["posting_id"]

    def observe_closure(self, posting_id, observed_at):
        changed = self.connection.execute(
            "UPDATE posting SET closed_observed_at = ? WHERE posting_id = ?",
            (timestamp(observed_at), posting_id),
        )
        if changed.rowcount != 1:
            raise ValueError("Unknown posting")

    def add_label(self, *, subject, subject_id, dimension, value, method, classifier_version, created_at=None):
        self.connection.execute("""
            INSERT INTO label (subject, subject_id, dimension, value, method, classifier_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (subject, subject_id, dimension, value, method, classifier_version,
              timestamp(created_at) if created_at is not None else self.now()))
