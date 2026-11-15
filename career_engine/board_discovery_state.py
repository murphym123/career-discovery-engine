"""Transactions for discovery checkpoints in Prospect's existing SQLite store."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

from career_engine.ats import PUBLIC_HOSTS
from career_engine.ats_registry import empty_registry, upsert_verified_board
from career_engine.board_discovery_http import Response
from career_engine.board_indexes import EVIDENCE_KINDS, INDEX_URLS, Page, Plan, board_probe_url, candidate, source_time
from career_engine.registry_state import read_registry, save_registry
from career_engine.store import Store
from career_engine.store.blobs import managed_path


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds")


def cursor_digest(cursor: str) -> str:
    return sha256(cursor.encode()).hexdigest()


class DiscoveryState:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.connection
        self.db.executescript(Path(__file__).with_name("store").joinpath("board_discovery.sql").read_text())

    @contextmanager
    def exclusive(self):
        """Single executor per store, with automatic release on process death."""
        lock = managed_path(self.store.path.parent, self.store.path.name + ".board-discovery.lock")
        with lock.open("a+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Board discovery is already active for this store") from None
            yield

    def setup(self, name: str, plan: Plan):
        encoded = json.dumps({**asdict(plan), "through": plan.through.astimezone(timezone.utc).isoformat()},
                             sort_keys=True)
        with self.store.transaction() as db:
            row = db.execute("SELECT plan FROM board_discovery_campaign WHERE name=?", (name,)).fetchone()
            if row:
                if row["plan"] != encoded:
                    raise ValueError("Campaign index parameters changed. Resume with the original plan or use a new campaign.")
                return
            db.execute("INSERT INTO board_discovery_campaign VALUES (?, ?)", (name, encoded))
            sources = ["wayback"]
            if plan.recent_days:
                sources.append("urlscan")
            if plan.commoncrawl:
                sources.append("commoncrawl")
            db.executemany("INSERT INTO board_discovery_stream(campaign, source, host) VALUES (?, ?, ?)",
                           ((name, source, host) for source in sources for host in PUBLIC_HOSTS))

    def streams(self, campaign: str):
        return self.db.execute("SELECT * FROM board_discovery_stream WHERE campaign=? ORDER BY last_attempt, id", (campaign,))

    def stream(self, identifier: int):
        return self.db.execute("SELECT * FROM board_discovery_stream WHERE id=?", (identifier,)).fetchone()

    def fallback_eligible(self, stream) -> bool:
        row = self.db.execute("""
            SELECT status FROM board_discovery_stream WHERE campaign=? AND source='wayback' AND host=?
        """, (stream["campaign"], stream["host"])).fetchone()
        return row["status"] in {"failed", "denied", "deferred", "empty"}

    def last_attempt(self) -> int:
        return self.db.execute("SELECT coalesce(max(id), 0) FROM board_discovery_attempt").fetchone()[0]

    def variants(self, origin: str, through: int):
        return self.db.execute("""
            SELECT v.* FROM board_discovery_variant v JOIN board_discovery_board b USING (board_key)
            WHERE v.origin=? AND v.last_attempt<=? AND v.status IN ('candidate', 'deferred', 'failed')
                AND b.verified_url IS NULL ORDER BY v.last_attempt, v.id
        """, (origin, through))

    def origin(self, origin: str) -> tuple[float, str]:
        row = self.db.execute("SELECT not_before, stop_reason FROM board_discovery_origin WHERE origin=?",
                              (origin,)).fetchone()
        return tuple(row) if row else (0, "")

    def set_origin(self, origin: str, not_before: float, stop: str):
        self.db.execute("""
            INSERT INTO board_discovery_origin VALUES (?, ?, ?)
            ON CONFLICT(origin) DO UPDATE SET
                not_before=max(not_before, excluded.not_before),
                stop_reason=coalesce(nullif(stop_reason, ''), excluded.stop_reason)
        """, (origin, not_before, stop))

    def robots(self):
        return self.db.execute("SELECT * FROM board_discovery_robots")

    def save_robots(self, origin, expires, rules):
        self.db.execute("""
            INSERT INTO board_discovery_robots VALUES (?, ?, ?, ?)
            ON CONFLICT(origin) DO UPDATE SET expires=excluded.expires, rules=excluded.rules, delay=excluded.delay
        """, (origin, expires, json.dumps(rules.rules), rules.delay))

    def seen_cursor(self, stream, cursor: str) -> bool:
        return cursor == stream["cursor"] or self.db.execute("""
            SELECT 1 FROM board_discovery_page WHERE stream=? AND cursor_digest=?
        """, (stream["id"], cursor_digest(cursor))).fetchone() is not None

    def set_stream(self, stream, status: str, reason: str, *, cursor=None, total=None):
        self.db.execute("""
            UPDATE board_discovery_stream SET status=?, reason=?, cursor=coalesce(?,cursor),
                total=coalesce(?,total) WHERE id=?
        """, (status, reason, cursor, total, stream["id"]))

    def record_page(self, stream, page: Page, now: str, *, advance=True) -> str:
        db = self.db
        rejected = page.rejected
        position = cursor_digest(stream["cursor"])
        for url, captured, record in page.rows:
            result = candidate(url, stream["host"])
            if result is None:
                rejected += 1
                continue
            board, observed = result
            key = board["key"]
            origin = urlsplit(board_probe_url(board)).hostname
            db.execute("""
                INSERT OR IGNORE INTO board_discovery_board(board_key, first_seen_at) VALUES (?, ?)
            """, (key, now))
            db.execute("""
                INSERT OR IGNORE INTO board_discovery_variant(board_key, url, origin, status)
                SELECT board_key, ?, ?, CASE WHEN verified_url IS NULL THEN 'candidate' ELSE 'superseded' END
                FROM board_discovery_board WHERE board_key=?
            """, (board["url"], origin, key))
            db.execute("""
                INSERT OR IGNORE INTO board_discovery_evidence
                    (board_key, stream, observed_url, source_time, source_record, cursor_digest, first_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (key, stream["id"], observed, source_time(captured), record, position, now))
        if not advance:
            return "failed"
        db.execute("INSERT INTO board_discovery_page VALUES (?, ?, ?, ?)",
                   (stream["id"], position, len(page.rows) + page.rejected, rejected))
        if page.cursor is not None:
            status = "candidate"
        else:
            any_evidence = db.execute("SELECT 1 FROM board_discovery_evidence WHERE stream=? LIMIT 1",
                                      (stream["id"],)).fetchone()
            status = "complete" if any_evidence else "empty"
        self.set_stream(stream, status, "page_recorded", cursor=page.cursor)
        return status

    def set_variant(self, variant, status: str, reason: str, method: str, now: str):
        self.db.execute("""
            UPDATE board_discovery_variant SET status=?, reason=?, method=? WHERE id=?
        """, (status, reason, method, variant["id"]))
        if status == "validated":
            self.db.execute("""
                UPDATE board_discovery_board SET verified_url=?, verified_at=?, verification=?
                WHERE board_key=? AND verified_url IS NULL
            """, (variant["url"], now, reason, variant["board_key"]))
            self.db.execute("""
                UPDATE board_discovery_variant SET status='superseded'
                WHERE board_key=? AND id!=? AND status IN ('candidate', 'deferred', 'failed')
            """, (variant["board_key"], variant["id"]))

    def attempt(self, invocation: str, campaign: str, method: str, endpoint: str, status: str,
                reason: str, response: Response, now: str, *, stream=None, variant=None, keep_turn=False):
        stream_id = stream["id"] if stream is not None else None
        position = cursor_digest(stream["cursor"]) if stream is not None else None
        attempt = self.db.execute("""
            INSERT INTO board_discovery_attempt(invocation,campaign,stream,cursor_digest,variant,method,endpoint,
                                                status,reason,http_status,bytes_read,at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (invocation, campaign, stream_id, position, variant, method, endpoint, status, reason,
              response.status, response.bytes_read, now)).lastrowid
        if stream_id is not None:
            self.db.execute("UPDATE board_discovery_stream SET last_attempt=? WHERE id=?", (attempt, stream_id))
        if variant is not None and not keep_turn:
            self.db.execute("UPDATE board_discovery_variant SET last_attempt=? WHERE id=?", (attempt, variant))

    def summary(self, campaign: str) -> dict:
        with self.store.transaction(write=False):
            return self._summary(campaign)

    def _summary(self, campaign: str) -> dict:
        if self.db.execute("SELECT 1 FROM board_discovery_campaign WHERE name=?", (campaign,)).fetchone() is None:
            raise ValueError("Unknown board discovery campaign")
        streams = [dict(row) for row in self.db.execute("""
            SELECT s.source,s.host,s.status,s.reason,count(p.stream) pages,
                   coalesce(sum(p.rows),0) rows,coalesce(sum(p.rejected),0) rejected
            FROM board_discovery_stream s LEFT JOIN board_discovery_page p ON p.stream=s.id
            WHERE s.campaign=? GROUP BY s.id ORDER BY s.id
        """, (campaign,))]
        wayback = {row["host"]: row for row in streams if row["source"] == "wayback"}
        for stream in streams:
            _, stop = self.origin(urlsplit(INDEX_URLS[stream["source"]]).hostname)
            if stop and stream["status"] in {"candidate", "deferred", "failed"}:
                stream.update(status="denied", reason=stop)
            elif (stream["source"] == "commoncrawl" and stream["status"] in {"candidate", "deferred", "failed"}
                  and wayback[stream["host"]]["status"] not in {"failed", "denied", "deferred", "empty"}):
                stream.update(status="standby", reason="wayback_preferred")
        counts = dict(self.db.execute("""
            WITH states AS (
                SELECT v.board_key, CASE
                    WHEN b.verified_url IS NOT NULL THEN 0
                    WHEN v.status='missing' THEN 5
                    WHEN o.stop_reason != '' OR v.status='denied' THEN 4
                    WHEN v.status='candidate' THEN 1
                    WHEN v.status='deferred' THEN 2
                    WHEN v.status='failed' THEN 3 ELSE 4 END priority
                FROM board_discovery_variant v JOIN board_discovery_board b USING (board_key)
                LEFT JOIN board_discovery_origin o USING (origin)
            ), boards AS (
                SELECT min(priority) AS status FROM states GROUP BY board_key
            ) SELECT status,count(*) FROM boards GROUP BY status
        """))
        names = ("validated", "candidate", "deferred", "failed", "denied", "missing")
        origins = [dict(row) for row in self.db.execute("""
            SELECT o.*, a.status AS robots_status, a.reason AS robots_reason, a.at AS robots_checked_at
            FROM board_discovery_origin o LEFT JOIN board_discovery_attempt a ON a.id=(
                SELECT max(id) FROM board_discovery_attempt WHERE endpoint='https://' || o.origin || '/robots.txt'
                    AND stream IS NULL AND variant IS NULL
            ) ORDER BY o.origin
        """)]
        return {"streams": streams, "boards": {name: counts.get(i, 0) for i, name in enumerate(names)},
                "origins": origins,
                "evidence": self.db.execute("SELECT count(*) FROM board_discovery_evidence").fetchone()[0]}

    def publish(self, registry_path: Path) -> dict:
        added = changed = 0
        with self.exclusive():
            registry = read_registry(registry_path) if registry_path.exists() else empty_registry()
            for row in self.db.execute("""
                SELECT b.*,e.id evidence_id,e.observed_url,e.source_time,e.source_record,s.source,s.host,c.plan
                FROM board_discovery_board b
                JOIN board_discovery_evidence e ON e.id=(SELECT min(id) FROM board_discovery_evidence WHERE board_key=b.board_key)
                JOIN board_discovery_stream s ON s.id=e.stream
                JOIN board_discovery_campaign c ON c.name=s.campaign
                WHERE b.verified_url IS NOT NULL ORDER BY b.board_key
            """):
                endpoint = INDEX_URLS[row["source"]]
                if row["source"] == "commoncrawl":
                    endpoint += json.loads(row["plan"])["commoncrawl"] + "-index"
                proof = {"kind": EVIDENCE_KINDS[row["source"]], "source": row["source"],
                         "url": endpoint, "pattern": row["host"],
                         "observed_url": row["observed_url"], "archive_timestamp": row["source_time"],
                         "source_record": row["source_record"], "source_id": row["board_key"]}
                verification = {"verified_at": row["verified_at"], "method": row["verification"],
                                "url": row["verified_url"], "evidence_id": row["evidence_id"]}
                new, updated = upsert_verified_board(registry, row["verified_url"], proof,
                                                     verification, row["first_seen_at"])
                added += new
                changed += updated
            if changed or not registry_path.exists():
                save_registry(registry_path, registry)
        return {"added": added, "updated": changed - added, "registry_boards": len(registry["boards"])}
