BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS employer (
    employer_id TEXT PRIMARY KEY NOT NULL,
    canonical_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'provisional', 'rejected', 'merged')),
    origin TEXT NOT NULL,
    merged_into TEXT REFERENCES employer(employer_id),
    CHECK ((status = 'merged') = (merged_into IS NOT NULL)),
    CHECK (employer_id <> merged_into)
);

CREATE TABLE IF NOT EXISTS employer_alias (
    employer_id TEXT NOT NULL REFERENCES employer(employer_id),
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (employer_id, kind, value)
);

CREATE TABLE IF NOT EXISTS frame (
    frame_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS frame_membership (
    frame_id TEXT NOT NULL REFERENCES frame(frame_id),
    employer_id TEXT NOT NULL REFERENCES employer(employer_id),
    added_at TEXT NOT NULL,
    removed_at TEXT,
    PRIMARY KEY (frame_id, employer_id, added_at)
);

CREATE TABLE IF NOT EXISTS channel (
    channel_id TEXT PRIMARY KEY NOT NULL,
    channel_key TEXT UNIQUE,
    employer_id TEXT REFERENCES employer(employer_id),
    kind TEXT NOT NULL,
    platform TEXT,
    locator TEXT NOT NULL,
    retention_class TEXT NOT NULL CHECK (retention_class IN ('public', 'private_email', 'licensed', 'restricted')),
    evidence_role TEXT NOT NULL,
    verification_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
    run_id TEXT PRIMARY KEY NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    code_sha TEXT NOT NULL,
    config_sha TEXT NOT NULL,
    trigger TEXT NOT NULL,
    arm TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_capture (
    raw_sha TEXT PRIMARY KEY NOT NULL,
    retention_class TEXT NOT NULL CHECK (retention_class = 'public'),
    captured_at TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0)
);

CREATE TABLE IF NOT EXISTS poll (
    poll_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    channel_id TEXT NOT NULL REFERENCES channel(channel_id),
    started_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'failed') OR status LIKE 'skipped:%'),
    declared_total INTEGER CHECK (declared_total >= 0),
    received INTEGER NOT NULL CHECK (received >= 0),
    unrepresented INTEGER NOT NULL DEFAULT 0 CHECK (unrepresented BETWEEN 0 AND received),
    raw_sha TEXT REFERENCES raw_capture(raw_sha),
    UNIQUE (poll_id, channel_id)
);

-- The primary inventory digest is on poll. The ordered response receipts also
-- record any additional pages, board metadata, and detail responses.
CREATE TABLE IF NOT EXISTS poll_capture (
    poll_id TEXT NOT NULL REFERENCES poll(poll_id),
    ordinal INTEGER NOT NULL,
    raw_sha TEXT NOT NULL REFERENCES raw_capture(raw_sha),
    role TEXT NOT NULL,
    PRIMARY KEY (poll_id, ordinal)
);

-- An entry distinguishes newly accounted exceptions from any inferred counts
-- in a database made with the earlier M1a review branch.
CREATE TABLE IF NOT EXISTS poll_accounting (
    poll_id TEXT PRIMARY KEY NOT NULL REFERENCES poll(poll_id),
    withheld_inventory_responses INTEGER NOT NULL CHECK (withheld_inventory_responses >= 0),
    capture_withheld TEXT CHECK (capture_withheld IS NULL OR
                                 (capture_withheld = 'secret_echo' AND withheld_inventory_responses > 0))
);

-- Ordered local receipts let an export verify which inventory pages were
-- retained, withheld, or unexplained, without recording a withheld digest.
CREATE TABLE IF NOT EXISTS poll_inventory (
    poll_id TEXT NOT NULL REFERENCES poll(poll_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    disposition TEXT NOT NULL CHECK (disposition IN ('retained', 'secret_echo', 'missing')),
    raw_sha TEXT REFERENCES raw_capture(raw_sha),
    sightings INTEGER NOT NULL CHECK (sightings >= 0),
    CHECK ((disposition = 'retained') = (raw_sha IS NOT NULL)),
    PRIMARY KEY (poll_id, ordinal)
);

CREATE TABLE IF NOT EXISTS posting (
    posting_id TEXT PRIMARY KEY NOT NULL,
    employer_id TEXT REFERENCES employer(employer_id),
    identity_key TEXT NOT NULL UNIQUE,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    closed_observed_at TEXT
);

CREATE TABLE IF NOT EXISTS sighting (
    sighting_id TEXT PRIMARY KEY NOT NULL,
    posting_id TEXT NOT NULL REFERENCES posting(posting_id),
    poll_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    native_id TEXT,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    employer_name_raw TEXT NOT NULL,
    location_raw TEXT NOT NULL,
    posted_at TEXT,
    closing_at TEXT,
    observed_at TEXT NOT NULL,
    description_sha TEXT,
    raw_sha TEXT REFERENCES raw_capture(raw_sha),
    FOREIGN KEY (poll_id, channel_id) REFERENCES poll(poll_id, channel_id)
);

CREATE TABLE IF NOT EXISTS label (
    label_id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL CHECK (subject IN ('run', 'channel', 'poll', 'employer', 'posting', 'sighting')),
    subject_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    value TEXT NOT NULL,
    method TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS poll_window ON poll (rtrim(started_at, 'Z'));
CREATE INDEX IF NOT EXISTS sighting_poll ON sighting (poll_id);
CREATE INDEX IF NOT EXISTS sighting_posting ON sighting (posting_id);
CREATE INDEX IF NOT EXISTS label_subject ON label (subject, subject_id);

CREATE TRIGGER IF NOT EXISTS label_validate_subject BEFORE INSERT ON label
WHEN NOT CASE NEW.subject
    WHEN 'run' THEN EXISTS (SELECT 1 FROM run WHERE run_id = NEW.subject_id)
    WHEN 'channel' THEN EXISTS (SELECT 1 FROM channel WHERE channel_id = NEW.subject_id)
    WHEN 'poll' THEN EXISTS (SELECT 1 FROM poll WHERE poll_id = NEW.subject_id)
    WHEN 'employer' THEN EXISTS (SELECT 1 FROM employer WHERE employer_id = NEW.subject_id)
    WHEN 'posting' THEN EXISTS (SELECT 1 FROM posting WHERE posting_id = NEW.subject_id)
    WHEN 'sighting' THEN EXISTS (SELECT 1 FROM sighting WHERE sighting_id = NEW.subject_id)
    ELSE 0
END
BEGIN
    SELECT RAISE(ABORT, 'label subject does not exist');
END;

CREATE TRIGGER IF NOT EXISTS label_no_replace BEFORE INSERT ON label
WHEN EXISTS (SELECT 1 FROM label WHERE label_id = NEW.label_id)
BEGIN
    SELECT RAISE(ABORT, 'labels are append-only');
END;

CREATE TRIGGER IF NOT EXISTS label_no_update BEFORE UPDATE ON label
BEGIN
    SELECT RAISE(ABORT, 'labels are append-only');
END;

CREATE TRIGGER IF NOT EXISTS label_no_delete BEFORE DELETE ON label
BEGIN
    SELECT RAISE(ABORT, 'labels are append-only');
END;

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
COMMIT;
