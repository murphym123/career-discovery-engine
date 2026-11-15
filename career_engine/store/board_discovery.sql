-- Opt-in discovery metadata in the existing Prospect database. No posting bodies.
CREATE TABLE IF NOT EXISTS board_discovery_campaign (
    name TEXT PRIMARY KEY, plan TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS board_discovery_stream (
    id INTEGER PRIMARY KEY,
    campaign TEXT NOT NULL REFERENCES board_discovery_campaign(name),
    source TEXT NOT NULL,
    host TEXT NOT NULL,
    cursor TEXT NOT NULL DEFAULT '',
    total INTEGER,
    status TEXT NOT NULL DEFAULT 'candidate',
    reason TEXT NOT NULL DEFAULT '',
    last_attempt INTEGER NOT NULL DEFAULT 0,
    UNIQUE (campaign, source, host)
);
CREATE TABLE IF NOT EXISTS board_discovery_page (
    stream INTEGER NOT NULL REFERENCES board_discovery_stream(id),
    cursor_digest TEXT NOT NULL,
    rows INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    PRIMARY KEY (stream, cursor_digest)
);
CREATE TABLE IF NOT EXISTS board_discovery_board (
    board_key TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    verified_url TEXT,
    verified_at TEXT,
    verification TEXT
);
CREATE TABLE IF NOT EXISTS board_discovery_variant (
    id INTEGER PRIMARY KEY,
    board_key TEXT NOT NULL REFERENCES board_discovery_board(board_key),
    url TEXT NOT NULL,
    origin TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'HEAD',
    status TEXT NOT NULL DEFAULT 'candidate',
    reason TEXT NOT NULL DEFAULT '',
    last_attempt INTEGER NOT NULL DEFAULT 0,
    UNIQUE (board_key, url)
);
CREATE INDEX IF NOT EXISTS board_discovery_queue ON board_discovery_variant(origin, last_attempt, id)
    WHERE status IN ('candidate', 'deferred', 'failed');
CREATE TABLE IF NOT EXISTS board_discovery_evidence (
    id INTEGER PRIMARY KEY,
    board_key TEXT NOT NULL REFERENCES board_discovery_board(board_key),
    stream INTEGER NOT NULL REFERENCES board_discovery_stream(id),
    observed_url TEXT NOT NULL,
    source_time TEXT NOT NULL,
    source_record TEXT NOT NULL,
    cursor_digest TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE (board_key, stream, observed_url, source_time, source_record)
);
CREATE INDEX IF NOT EXISTS board_discovery_evidence_stream ON board_discovery_evidence(stream);
CREATE TABLE IF NOT EXISTS board_discovery_robots (
    origin TEXT PRIMARY KEY,
    expires REAL NOT NULL,
    rules TEXT NOT NULL,
    delay REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS board_discovery_origin (
    origin TEXT PRIMARY KEY,
    not_before REAL NOT NULL DEFAULT 0,
    stop_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS board_discovery_attempt (
    id INTEGER PRIMARY KEY,
    invocation TEXT NOT NULL,
    campaign TEXT NOT NULL REFERENCES board_discovery_campaign(name),
    stream INTEGER REFERENCES board_discovery_stream(id),
    cursor_digest TEXT,
    variant INTEGER REFERENCES board_discovery_variant(id),
    method TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    bytes_read INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS board_discovery_attempt_robots ON board_discovery_attempt(endpoint, id)
    WHERE stream IS NULL AND variant IS NULL;

-- Static catalogs are source evidence, independent of URL index campaigns.
CREATE TABLE IF NOT EXISTS board_catalog_import (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    blocklist_sha256 TEXT NOT NULL,
    metadata TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    counts TEXT NOT NULL,
    UNIQUE (source, url, sha256, blocklist_sha256)
);
CREATE TABLE IF NOT EXISTS board_catalog_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch INTEGER NOT NULL REFERENCES board_catalog_import(id),
    line INTEGER NOT NULL,
    board_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    raw_provider TEXT NOT NULL,
    company TEXT NOT NULL,
    raw_slug TEXT NOT NULL,
    record_date TEXT NOT NULL,
    source_status TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('candidate', 'dead', 'unsupported')),
    variant_url TEXT NOT NULL,
    observed_url TEXT NOT NULL,
    source_evidence TEXT NOT NULL,
    UNIQUE (batch, line)
);
CREATE INDEX IF NOT EXISTS board_catalog_evidence_board ON board_catalog_evidence(board_key, variant_url, disposition);
CREATE INDEX IF NOT EXISTS board_catalog_evidence_version ON board_catalog_evidence(board_key, batch);
CREATE VIEW IF NOT EXISTS board_catalog_current AS
SELECT e.* FROM board_catalog_evidence e JOIN board_catalog_import i ON i.id=e.batch
WHERE NOT EXISTS (
    SELECT 1 FROM board_catalog_evidence newer JOIN board_catalog_import j ON j.id=newer.batch
    WHERE newer.board_key=e.board_key AND newer.batch>e.batch AND j.source=i.source
);
CREATE TABLE IF NOT EXISTS board_catalog_block (
    board_key TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS board_catalog_withdrawal (
    batch INTEGER NOT NULL REFERENCES board_catalog_import(id),
    line INTEGER NOT NULL,
    board_key TEXT NOT NULL,
    metadata_rejection TEXT NOT NULL,
    PRIMARY KEY (batch, line)
);
