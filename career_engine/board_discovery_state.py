"""Transactions for discovery checkpoints in Prospect's existing SQLite store."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from career_engine.ats import PUBLIC_HOSTS
from career_engine.ats_registry import CATALOG_KIND, PROVIDER_ORIGINS, empty_registry, upsert_verified_board
from career_engine.board_catalogs import CATALOG_CAMPAIGN, SOURCES, catalog_source_evidence
from career_engine.board_discovery_http import Response
from career_engine.board_indexes import (
    EVIDENCE_KINDS, INDEX_URLS, Page, Plan, board_probe_url, candidate, commoncrawl_capture, source_time, wire_cursor,
)
from career_engine.registry_state import read_registry, registry_lock, save_registry
from career_engine.store import Store
from career_engine.store.blobs import managed_path


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds")


def cursor_digest(cursor: str) -> str:
    return sha256(cursor.encode()).hexdigest()


CURRENT_CATALOG_VARIANT = """EXISTS (
    SELECT 1 FROM board_catalog_current e WHERE e.board_key=v.board_key
    AND e.variant_url=v.url AND e.disposition='candidate'
)"""
# A catalog observation can govern catalog eligibility while independent index
# evidence remains usable in ordinary discovery.
INDEX_SCOPE = """(
    EXISTS (SELECT 1 FROM board_discovery_evidence e WHERE e.board_key=v.board_key AND e.eligible=1)
)"""
def crawl_window(encoded_plan):
    plan = json.loads(encoded_plan)
    return plan["archive_from"], datetime.fromisoformat(plan["through"]).strftime("%Y%m%d%H%M%S")


class DiscoveryState:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.connection
        self.db.executescript(Path(__file__).with_name("store").joinpath("board_discovery.sql").read_text())
        with self.store.transaction() as db:
            if "out_of_window" not in {column["name"] for column in db.execute("PRAGMA table_info(board_discovery_page)")}:
                db.execute("ALTER TABLE board_discovery_page ADD COLUMN out_of_window INTEGER NOT NULL DEFAULT 0")
            if "eligible" not in {column["name"] for column in db.execute("PRAGMA table_info(board_discovery_evidence)")}:
                db.execute("ALTER TABLE board_discovery_evidence ADD COLUMN eligible INTEGER NOT NULL DEFAULT 1")
                # Keep legacy evidence inspectable while excluding invalid captures from the queue and registry.
                db.create_function("prospect_capture", 3, commoncrawl_capture)
                try:
                    for campaign, encoded in db.execute("SELECT name, plan FROM board_discovery_campaign"):
                        db.execute("""
                            UPDATE board_discovery_evidence SET eligible=0
                            WHERE stream IN (
                                SELECT id FROM board_discovery_stream WHERE campaign=? AND source='commoncrawl'
                            ) AND prospect_capture(source_time, ?, ?) != ''
                        """, (campaign, *crawl_window(encoded)))
                finally:
                    db.create_function("prospect_capture", 3, None)
            # Previously published HEAD evidence is provisional. Resume at GET, retaining its attempts.
            db.execute("""
                UPDATE board_discovery_variant SET status='deferred', method='GET', reason='head_requires_get'
                WHERE status='validated' AND reason='head_reachable'
            """)
            db.execute("""
                UPDATE board_discovery_variant SET status='candidate'
                WHERE status='superseded' AND board_key IN (
                    SELECT board_key FROM board_discovery_board WHERE verification='head_reachable'
                )
            """)
            db.execute("""
                UPDATE board_discovery_board SET verified_url=NULL, verified_at=NULL, verification=NULL
                WHERE verification='head_reachable'
            """)

    @contextmanager
    def exclusive(self):
        """Single executor per store, with automatic release on process death."""
        lock = managed_path(self.store.path.parent, self.store.path.name + ".board-discovery.lock")
        with lock.open("a+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Board discovery is already active for this store") from None
            yield handle.fileno()

    def access_policy(self, routes, *, replace=False):
        """Pin one policy for all campaigns sharing the validation queue."""
        encoded = json.dumps(routes)
        with self.store.transaction() as db:
            row = db.execute("SELECT denied_routes FROM board_discovery_policy").fetchone()
            if row and not replace:
                if routes and row[0] != encoded:
                    raise ValueError("Discovery restrictions changed. Use --replace-access-policy to replace them.")
                return tuple(map(tuple, json.loads(row[0])))
            if row is None and not replace and db.execute(
                "SELECT 1 FROM board_discovery_campaign WHERE name!=? LIMIT 1", (CATALOG_CAMPAIGN,)
            ).fetchone():
                raise ValueError("Legacy campaign has no saved restrictions. Supply the full policy with --replace-access-policy.")
            db.execute("INSERT OR REPLACE INTO board_discovery_policy VALUES (1, ?)", (encoded,))
            if replace:
                for table in ("board_discovery_stream", "board_discovery_variant"):
                    db.execute(f"""
                        UPDATE {table} SET status='deferred', reason='operator_policy_recheck'
                        WHERE status='denied' AND reason='operator_policy'
                    """)
        return routes

    def setup(self, name: str, plan: Plan):
        if name == CATALOG_CAMPAIGN:
            raise ValueError("Use the public catalog CLI for the reserved catalog campaign")
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

    def variants(self, origin: str, through: int, *, catalog_only=False, robots_expired=False):
        scope = CURRENT_CATALOG_VARIANT if catalog_only else f"({CURRENT_CATALOG_VARIANT} OR {INDEX_SCOPE})"
        robots_denials = "OR (v.status='denied' AND v.reason LIKE 'robots: %')" if robots_expired else ""
        return self.db.execute(f"""
            SELECT v.* FROM board_discovery_variant v JOIN board_discovery_board b USING (board_key)
            WHERE v.origin=? AND v.last_attempt<=? AND (
                v.status IN ('candidate', 'deferred', 'failed') {robots_denials}
            ) AND b.verified_url IS NULL
                AND NOT EXISTS (SELECT 1 FROM board_catalog_block q WHERE q.board_key=v.board_key)
                AND {scope}
            ORDER BY v.last_attempt, v.id
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

    def dispatch_after(self, origin):
        row = self.db.execute("SELECT not_before FROM board_discovery_dispatch WHERE origin=?", (origin,)).fetchone()
        return row[0] if row else 0

    def set_dispatch_after(self, origin, not_before):
        self.db.execute("""
            INSERT INTO board_discovery_dispatch VALUES (?, ?)
            ON CONFLICT(origin) DO UPDATE SET not_before=excluded.not_before
        """, (origin, not_before))

    def robots(self):
        return self.db.execute("SELECT * FROM board_discovery_robots")

    def save_robots(self, origin, expires, rules):
        self.db.execute("""
            INSERT INTO board_discovery_robots VALUES (?, ?, ?, ?)
            ON CONFLICT(origin) DO UPDATE SET expires=excluded.expires, rules=excluded.rules, delay=excluded.delay
        """, (origin, expires, json.dumps(rules.rules), rules.delay))
        self.db.execute("""
            UPDATE board_discovery_variant SET status='deferred', reason='robots_recheck'
            WHERE origin=? AND status='denied' AND reason LIKE 'robots: %'
        """, (origin,))
        for source, url in INDEX_URLS.items():
            if urlsplit(url).hostname == origin:
                self.db.execute("""
                    UPDATE board_discovery_stream SET status='deferred', reason='robots_recheck'
                    WHERE source=? AND status='denied' AND reason LIKE 'robots: %'
                """, (source,))

    def seen_cursor(self, stream, cursor: str) -> bool:
        wire = wire_cursor(stream["source"], cursor)
        return wire == wire_cursor(stream["source"], stream["cursor"]) or self.db.execute("""
            SELECT 1 FROM board_discovery_page WHERE stream=? AND cursor_digest IN (?,?)
        """, (stream["id"], self._position(stream, cursor), cursor_digest(cursor))).fetchone() is not None

    def _position(self, stream, cursor=None):
        cursor = stream["cursor"] if cursor is None else cursor
        if stream["source"] == "wayback" and cursor:
            # Namespace wire digests away from legacy opaque-key digests.
            return cursor_digest("wayback-wire\0" + wire_cursor("wayback", cursor))
        return cursor_digest(cursor)

    def set_stream(self, stream, status: str, reason: str, *, cursor=None, total=None):
        self.db.execute("""
            UPDATE board_discovery_stream SET status=?, reason=?, cursor=coalesce(?,cursor),
                total=coalesce(?,total) WHERE id=?
        """, (status, reason, cursor, total, stream["id"]))

    def _add_variant(self, board, now):
        self.db.execute("""
            INSERT OR IGNORE INTO board_discovery_board(board_key, first_seen_at) VALUES (?, ?)
        """, (board["key"], now))
        self.db.execute("""
            INSERT OR IGNORE INTO board_discovery_variant(board_key, url, origin, status)
            SELECT board_key, ?, ?, CASE WHEN verified_url IS NULL THEN 'candidate' ELSE 'superseded' END
            FROM board_discovery_board WHERE board_key=?
        """, (board["url"], urlsplit(board_probe_url(board)).hostname, board["key"]))

    def record_page(self, stream, page: Page, now: str, *, advance=True) -> str:
        db = self.db
        out_of_window = 0
        window = None
        if stream["source"] == "commoncrawl":
            window = crawl_window(db.execute("SELECT plan FROM board_discovery_campaign WHERE name=?",
                                             (stream["campaign"],)).fetchone()[0])
        rejected = page.rejected
        position = self._position(stream)
        for url, captured, record in page.rows:
            if window is not None:
                rejection = commoncrawl_capture(captured, *window)
                if rejection:
                    out_of_window += int(rejection == "out_of_window")
                    rejected += 1
                    continue
            result = candidate(url, stream["host"])
            if result is None:
                rejected += 1
                continue
            board, observed = result
            key = board["key"]
            self._add_variant(board, now)
            db.execute("""
                INSERT OR IGNORE INTO board_discovery_evidence
                    (board_key, stream, observed_url, source_time, source_record, cursor_digest, first_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (key, stream["id"], observed, source_time(captured), record, position, now))
        if not advance:
            return "failed"
        db.execute("""
            INSERT INTO board_discovery_page(stream,cursor_digest,rows,rejected,out_of_window) VALUES (?, ?, ?, ?, ?)
        """, (stream["id"], position, len(page.rows) + page.rejected, rejected, out_of_window))
        if page.cursor is not None:
            status = "candidate"
        else:
            any_evidence = db.execute("SELECT 1 FROM board_discovery_evidence WHERE stream=? AND eligible=1 LIMIT 1",
                                      (stream["id"],)).fetchone()
            status = "complete" if any_evidence else "empty"
        self.set_stream(stream, status, "page_recorded", cursor=page.cursor)
        return status

    def require_catalog_campaign(self):
        if self.db.execute("SELECT 1 FROM board_discovery_campaign WHERE name=?",
                           (CATALOG_CAMPAIGN,)).fetchone() is None:
            raise ValueError("Import a catalog manifest before running catalog validation")

    def record_catalogs(self, batches, blocks):
        """Record verified object hashes, policy and candidate evidence atomically."""
        now = self.store.now()
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO board_discovery_campaign VALUES (?, ?)",
                       (CATALOG_CAMPAIGN, '{"kind":"public_catalog"}'))
            db.executemany("INSERT OR IGNORE INTO board_catalog_block VALUES (?, ?, ?, ?)",
                           ((key, *proof, now) for key, proof in sorted(blocks.items())))
            blocked = {row[0] for row in db.execute("SELECT board_key FROM board_catalog_block")}
            db.executemany("DELETE FROM board_catalog_evidence WHERE board_key=?", ((key,) for key in blocks))
            for catalog, data_hash, policy_hash, rows, rejected in batches:
                source = SOURCES[catalog.source_id]
                metadata = {"source_name": source.name, "revision": catalog.revision, "license": source.license,
                            "license_url": source.license_url, "attribution": source.attribution,
                            "attribution_url": source.attribution_url,
                            "data_input": "local_file" if catalog.data.file else "github_raw",
                            "changes": "Prospect normalized and deduplicated catalog observations."}
                if catalog.blocklist:
                    metadata["blocklist_url"] = catalog.blocklist.url
                    metadata["blocklist_input"] = "local_file" if catalog.blocklist.file else "github_raw"
                counts = Counter(row.disposition if row.board_key not in blocked else "blocked" for row in rows)
                withdrawal_rejections = Counter(row.rejection for row in rows if row.rejection)
                insert = db.execute("""
                    INSERT OR IGNORE INTO board_catalog_import
                        (source,url,sha256,blocklist_sha256,metadata,imported_at,counts)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (catalog.source_id, catalog.data.url, data_hash, policy_hash, json.dumps(metadata), now,
                      json.dumps({"rows": len(rows) + sum(rejected.values()), "rejected": rejected,
                                  "withdrawal_metadata_rejections": withdrawal_rejections,
                                  "providers": Counter(row.provider for row in rows), **counts})))
                batch, = db.execute("""
                    SELECT id FROM board_catalog_import WHERE source=? AND url=? AND sha256=? AND blocklist_sha256=?
                """, (catalog.source_id, catalog.data.url, data_hash, policy_hash)).fetchone()
                db.executemany("INSERT OR IGNORE INTO board_catalog_withdrawal VALUES (?, ?, ?, ?)",
                               ((batch, row.line, row.board_key, row.rejection)
                                for row in rows if row.disposition == "withdrawn"))
                if not insert.rowcount or db.execute("""
                    SELECT 1 FROM board_catalog_import WHERE source=? AND url=? AND sha256=? AND id<?
                """, (catalog.source_id, catalog.data.url, data_hash, batch)).fetchone():
                    continue
                for row in rows:
                    if row.board_key in blocked:
                        continue
                    db.execute("""
                        INSERT INTO board_catalog_evidence
                            (batch,line,board_key,provider,raw_provider,company,raw_slug,record_date,source_status,
                             disposition,variant_url,observed_url,source_evidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (batch, row.line, row.board_key, row.provider, row.raw_provider, row.company,
                          row.raw_slug, row.record_date, row.source_status, row.disposition, row.variant_url,
                          row.observed_url, json.dumps(row.source_evidence)))
                    if row.disposition == "candidate":
                        self._add_variant({"key": row.board_key, "url": row.variant_url, "provider": row.provider}, now)
                        # Fresh positive evidence gives a missing route one more
                        # turn. The previous result remains in the HTTP attempt log.
                        db.execute("""
                            UPDATE board_discovery_variant SET status='candidate', reason='', method='HEAD'
                            WHERE board_key=? AND url=? AND status='missing'
                              AND EXISTS (SELECT 1 FROM board_discovery_board b
                                          WHERE b.board_key=? AND b.verified_url IS NULL)
                        """, (row.board_key, row.variant_url, row.board_key))

    def set_variant(self, variant, status: str, reason: str, method: str, now: str):
        if status == "validated" and (method, reason) != ("GET", "get_board_schema"):
            raise ValueError("Board verification requires a confirmed GET schema")
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
        position = self._position(stream) if stream is not None else None
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
        catalog_only = campaign == CATALOG_CAMPAIGN
        if self.db.execute("SELECT 1 FROM board_discovery_campaign WHERE name=?", (campaign,)).fetchone() is None:
            raise ValueError("Unknown board discovery campaign")
        streams = [dict(row) for row in self.db.execute("""
            SELECT s.source,s.host,s.status,s.reason,count(p.stream) pages,
                   coalesce(sum(p.rows),0) rows,coalesce(sum(p.rejected),0) rejected,
                   coalesce(sum(p.out_of_window),0) out_of_window
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
        scope = CURRENT_CATALOG_VARIANT if catalog_only else f"({CURRENT_CATALOG_VARIANT} OR {INDEX_SCOPE})"
        counts = dict(self.db.execute(f"""
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
                WHERE NOT EXISTS (SELECT 1 FROM board_catalog_block q WHERE q.board_key=v.board_key)
                  AND {scope}
            ), boards AS (
                SELECT min(priority) AS status FROM states GROUP BY board_key
            ) SELECT status,count(*) FROM boards GROUP BY status
        """))
        names = ("validated", "candidate", "deferred", "failed", "denied", "missing")
        origins = [dict(row) for row in self.db.execute("""
            WITH origins AS (
                SELECT origin FROM board_discovery_origin UNION SELECT origin FROM board_discovery_dispatch
            )
            SELECT origins.origin, max(coalesce(o.not_before,0),coalesce(d.not_before,0)) AS not_before,
                   coalesce(o.stop_reason,'') AS stop_reason,
                   a.status AS robots_status, a.reason AS robots_reason, a.at AS robots_checked_at
            FROM origins LEFT JOIN board_discovery_origin o USING (origin)
            LEFT JOIN board_discovery_dispatch d USING (origin)
            LEFT JOIN board_discovery_attempt a ON a.id=(
                SELECT max(id) FROM board_discovery_attempt WHERE endpoint='https://' || origins.origin || '/robots.txt'
                    AND stream IS NULL AND variant IS NULL
            ) ORDER BY origins.origin
        """)]
        result = {"streams": streams, "boards": {name: counts.get(i, 0) for i, name in enumerate(names)},
                  "origins": origins,
                  "evidence": self.db.execute("SELECT count(*) FROM board_discovery_evidence WHERE eligible=1").fetchone()[0],
                  "legacy_ineligible_evidence": self.db.execute(
                      "SELECT count(*) FROM board_discovery_evidence WHERE eligible=0"
                  ).fetchone()[0]}
        if catalog_only:
            imports = []
            for row in self.db.execute("SELECT * FROM board_catalog_import ORDER BY id"):
                entry = dict(row)
                entry.update(json.loads(entry.pop("metadata")))
                entry["counts"] = json.loads(entry["counts"])
                imports.append(entry)
            result["evidence"] = self.db.execute("SELECT count(*) FROM board_catalog_evidence").fetchone()[0]
            result["imports"] = imports
            result["blocked_identities"] = self.db.execute("SELECT count(*) FROM board_catalog_block").fetchone()[0]
            result["withdrawals"] = dict(self.db.execute("""
                SELECT coalesce(nullif(w.metadata_rejection, ''), 'recorded'),count(*)
                FROM board_catalog_withdrawal w GROUP BY w.metadata_rejection
            """))
            effective = dict(self.db.execute("""
                SELECT disposition,count(*) FROM (
                    SELECT CASE WHEN sum(disposition='candidate')>0 THEN 'candidate'
                                WHEN sum(disposition='dead')>0 THEN 'dead' ELSE 'unsupported' END disposition
                    FROM board_catalog_current GROUP BY board_key
                ) GROUP BY disposition
            """))
            result["effective_catalog_status"] = {
                name: effective.get(name, 0) for name in ("candidate", "dead", "unsupported")
            }
            result["unsupported"] = dict(self.db.execute("""
                SELECT provider,count(DISTINCT board_key) FROM board_catalog_current
                WHERE disposition='unsupported' GROUP BY provider
            """))
        return result

    def unsupported_catalogs(self):
        return [dict(row) for row in self.db.execute("""
            SELECT e.board_key,e.provider,e.company,e.raw_slug,e.record_date,e.source_status,e.line,
                   i.source,i.url,i.sha256
            FROM board_catalog_current e JOIN board_catalog_import i ON i.id=e.batch
            WHERE e.disposition='unsupported' ORDER BY e.board_key,e.id
        """)]

    def _catalog_proofs(self, key):
        for row in self.db.execute("""
            SELECT e.*,i.source,i.url,i.sha256,i.blocklist_sha256,i.metadata
            FROM board_catalog_evidence e JOIN board_catalog_import i ON i.id=e.batch
            WHERE e.board_key=? AND e.disposition='candidate' ORDER BY e.id
        """, (key,)):
            refs = json.loads(row["source_evidence"])
            yield {"kind": CATALOG_KIND, **json.loads(row["metadata"]),
                   "source": row["source"], "url": row["url"], "sha256": row["sha256"],
                   "blocklist_sha256": row["blocklist_sha256"], "source_id": key,
                   "provider": row["provider"], "raw_provider": row["raw_provider"],
                   "company": row["company"], "raw_slug": row["raw_slug"], "record_date": row["record_date"],
                   "source_status": row["source_status"], "board_url": row["variant_url"],
                   "observed_url": row["observed_url"], "source_record": row["line"],
                   "source_evidence": catalog_source_evidence(refs),
                   "catalog_evidence_id": row["id"]}

    def publish(self, registry_path: Path, *, catalog_only=False) -> dict:
        added = 0
        changed = set()
        with self.exclusive(), registry_lock(registry_path) as registry_path:
            if catalog_only:
                self.require_catalog_campaign()
            registry = read_registry(registry_path) if registry_path.exists() else empty_registry()
            pending = 0
            for entry in registry["boards"].values():
                verification = entry.get("discovery_verification")
                if isinstance(verification, dict) and verification.get("method") == "head_reachable":
                    verification["method"] = "pending_get"
                    pending += 1
            blocks = dict.fromkeys((row[0] for row in self.db.execute("SELECT board_key FROM board_catalog_block")), True)
            policy = {**registry.get("catalog_blocked_boards", {}), **blocks}
            policy_changed = policy != registry.get("catalog_blocked_boards", {})
            if policy_changed:
                registry["catalog_blocked_boards"] = policy
            holds = dict(registry.get("provider_origin_holds", {}))
            now = time.time()
            for row in self.db.execute("SELECT * FROM board_discovery_origin"):
                origin = row["origin"]
                if origin not in PROVIDER_ORIGINS.values():
                    continue
                if row["stop_reason"] or row["not_before"] > now:
                    holds[origin] = {"stop_reason": row["stop_reason"], "not_before": row["not_before"]}
                else:
                    holds.pop(origin, None)
            holds_changed = holds != registry.get("provider_origin_holds", {})
            if holds_changed:
                registry["provider_origin_holds"] = holds
            catalog_status = dict(self.db.execute("""
                SELECT board_key,max(disposition='candidate') FROM board_catalog_current GROUP BY board_key
            """))
            for key in policy.keys() & registry["boards"].keys():
                entry = registry["boards"][key]
                retained = [proof for proof in entry["evidence"] if proof.get("kind") != CATALOG_KIND]
                if retained != entry["evidence"]:
                    entry["evidence"] = retained
                    verification = entry.get("discovery_verification")
                    if isinstance(verification, dict) and "catalog_evidence_id" in verification:
                        del entry["discovery_verification"]
                    changed.add(key)
            for key, entry in registry["boards"].items():
                if key in policy:
                    continue
                has_catalog = False
                for index, proof in enumerate(entry["evidence"]):
                    if proof.get("kind") != CATALOG_KIND:
                        continue
                    has_catalog = True
                    refs = proof.get("source_evidence")
                    if isinstance(refs, dict):
                        cleaned = catalog_source_evidence(refs)
                        if cleaned != refs:
                            entry["evidence"][index] = {**proof, "source_evidence": cleaned}
                            changed.add(key)
                if key in catalog_status and has_catalog:
                    inactive = not catalog_status[key]
                    if inactive != entry.get("catalog_inactive", False):
                        if inactive:
                            entry["catalog_inactive"] = True
                        else:
                            del entry["catalog_inactive"]
                        changed.add(key)
            for row in self.db.execute("""
                SELECT b.*,e.id evidence_id,e.observed_url,e.source_time,e.source_record,s.source,s.host,c.plan
                FROM board_discovery_board b
                LEFT JOIN board_discovery_evidence e ON e.id=(SELECT min(id) FROM board_discovery_evidence
                       WHERE board_key=b.board_key AND eligible=1)
                LEFT JOIN board_discovery_stream s ON s.id=e.stream
                LEFT JOIN board_discovery_campaign c ON c.name=s.campaign
                WHERE b.verified_url IS NOT NULL AND b.verification='get_board_schema'
                  AND NOT EXISTS (SELECT 1 FROM board_catalog_block q WHERE q.board_key=b.board_key)
                ORDER BY b.board_key
            """):
                key = row["board_key"]
                origin = PROVIDER_ORIGINS[key.partition(":")[0]]
                catalog_active = catalog_status.get(key)
                if (key in policy or holds.get(origin, {}).get("stop_reason")
                        or catalog_only and not catalog_active
                        or not catalog_active and not row["source"]):
                    continue
                catalog_proofs = list(self._catalog_proofs(key)) if catalog_active else []
                proof = None
                if row["source"]:
                    endpoint = INDEX_URLS[row["source"]]
                    if row["source"] == "commoncrawl":
                        endpoint += json.loads(row["plan"])["commoncrawl"] + "-index"
                    proof = {"kind": EVIDENCE_KINDS[row["source"]], "source": row["source"],
                             "url": endpoint, "pattern": row["host"], "observed_url": row["observed_url"],
                             "archive_timestamp": row["source_time"], "source_record": row["source_record"],
                             "source_id": row["board_key"]}
                if not proof and not catalog_proofs:
                    continue
                verification = {"verified_at": row["verified_at"], "method": row["verification"],
                                "url": row["verified_url"]}
                if proof:
                    verification["evidence_id"] = row["evidence_id"]
                else:
                    selected = next((item for item in catalog_proofs if item["board_url"] == row["verified_url"]),
                                    catalog_proofs[0])
                    verification["catalog_evidence_id"] = selected["catalog_evidence_id"]
                new, updated = upsert_verified_board(registry, row["verified_url"], proof,
                                                     verification, row["first_seen_at"],
                                                     catalog_evidence=catalog_proofs)
                added += new
                if updated:
                    changed.add(key)
            if changed or policy_changed or holds_changed or pending or not registry_path.exists():
                save_registry(registry_path, registry)
        return {"added": added, "updated": len(changed) - added, "registry_boards": len(registry["boards"])}
