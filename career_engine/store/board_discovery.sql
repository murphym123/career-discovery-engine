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
