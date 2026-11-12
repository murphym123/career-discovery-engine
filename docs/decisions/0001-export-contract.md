# 0001 — Prospect export contract v1

**Status:** M0.1 approved as the foundation for development by the product owner.
**Authority:** [Prospect M0–M2 implementation specification](../specs/prospect-m0-m2-spec.md), especially §§4–5 and §8.1. The specification's review-era text is preserved verbatim. This record defines the exchange details and applies the approval clarifications on blinding, reproducibility, and execution.

## Decision

`prospect-export/1` is the shared interface between Prospect discovery and the evaluation repository. Prospect supplies an immutable snapshot with collection evidence and label history. Evaluation validates it and matches independently collected reference evidence. A contract change requires a new decision record and a version bump.

### Bundle and fields

A live bundle is a directory named `exports/<snapshot_id>/`. It contains `manifest.json` as one JSON object and all seven files below as UTF-8 JSON Lines (one object per line). An empty table has an empty file. The [shared synthetic bundle](../../fixtures/export-v1-example/) is a named test copy of this layout.

The manifest requires `contract_version` (exactly `prospect-export/1`), `snapshot_id` (ULID), `created_at`, `market_id` (for example `dc`), `prospect_code_sha` (Git commit SHA), `store_schema_version` (integer), `classifier_versions` (object mapping dimensions to version strings, with `"none"` for an unused classifier), `window` (object with `from` and `to`), `run_ids` (sorted array of IDs in `runs.jsonl`), and `files` (object mapping each of the seven data filenames to `{"rows": <count>, "sha256": "<digest>"}`). Hash each file's exact bytes, including line endings, and count its data records. The manifest has no self-hash.

| File | Required fields | Row sort key |
| --- | --- | --- |
| `runs.jsonl` | `run_id`, `started_at`, `finished_at`, `code_sha`, `config_sha`, `trigger`, `arm` | `run_id` |
| `channels.jsonl` | `channel_id`, `employer_id\|null`, `kind`, `platform\|null`, `locator`, `retention_class`, `evidence_role`, `verification_state` | `channel_id` |
| `polls.jsonl` | `poll_id`, `run_id`, `channel_id`, `started_at`, `status`, `declared_total\|null`, `received`, `raw_sha\|null` | `poll_id` |
| `employers.jsonl` | `employer_id`, `canonical_name`, `status`, `origin`, `merged_into\|null`, `aliases` (array of objects with `kind` and `value`) | `employer_id` |
| `postings.jsonl` | `posting_id`, `employer_id\|null`, `identity_key`, `first_seen`, `last_seen`, `closed_observed_at\|null` | `posting_id` |
| `sightings.jsonl` | `sighting_id`, `posting_id`, `poll_id`, `channel_id`, `native_id\|null`, `url`, `canonical_url`, `title`, `employer_name_raw`, `location_raw`, `posted_at\|null`, `closing_at\|null`, `observed_at`, `description_sha\|null`, `raw_sha\|null`, `raw_locator\|null` | `sighting_id` |
| `labels.jsonl` | `subject`, `subject_id`, `dimension`, `value`, `method`, `classifier_version`, `created_at` | `(subject, subject_id, dimension, created_at, classifier_version, method, value)` |

All listed keys must be present, including keys whose value may be `null`. Unqualified scalar fields are strings, except `declared_total` and `received`, which are nonnegative integers when present. Use UTC ISO-8601 timestamps ending in `Z` and lowercase hex for SHA-256 digests. Sort IDs and label tuples lexically. Consumers ignore additional fields they do not recognize. Open vocabularies such as `kind`, `method`, `trigger` and `arm` are strings, with examples in the fixture.

### Identity, evidence, and time

- Keep entity IDs stable for the life of the same record across snapshots from the same store lineage. Treat row IDs as opaque strings, with uniqueness within each entity file. A merged employer keeps its old row and points to its successor through `merged_into`. Only a `merged` row has a non-null target. The employer statuses are `active`, `provisional`, `rejected`, and `merged`.
- Use `<platform>:<tenant>:<native_id>` as the posting identity key when the platform, tenant, and native ID are shareable, and `url:<canonical_url>` otherwise. Start from `career_engine.ats.posting_key` and `career_engine.ats.canonical_posting_url`. `canonical_url` records Prospect's normalization. Evaluation independently normalizes the shareable sighting `url` and the reference URL using its own rules.
- Include every sighting from an exportable poll with `window.from <= poll.started_at < window.to`, including duplicates across polls and postings labeled `elsewhere`, `unknown`, or out of scope. Include the referenced runs, channels, postings, employers, and merge targets. A posting's first and last seen times summarize its store history at snapshot capture and may extend beyond the selected polls. `closed_observed_at` is the time closure was observed, distinct from an advertised `closing_at`.
- Preserve poll status as `ok`, `partial`, `failed`, or `skipped:<reason>`. Use a short, non-sensitive reason code. `declared_total` is the source's reported total, or `null` when unavailable. `received` counts records returned by the poll. Preserve failed, skipped, and zero-result exportable polls. All non-null references must resolve inside the bundle. A sighting's `channel_id` must match its poll. Label `subject` is the singular entity name (`run`, `channel`, `poll`, `employer`, `posting`, or `sighting`), and `subject_id` points to a row of that type.
- Export all label versions for included subjects. Geography labels use `dc`, `dmv_md`, `dmv_va`, `remote_us_eligible`, `remote_eligibility_unknown`, `elsewhere`, or `unknown`. The first four count as DC-eligible. Retain both uncertainty values distinctly from `elsewhere`. The scope codebook (M0.3) owns the live `in_scope` rules.

### Safe to exchange

Use an explicit projection of shareable metadata. Apply it to every field, including optional fields, aliases, locators, identity keys, configuration metadata, and manifest metadata. Strip private email addresses and headers, credentials, subscription and unsubscribe tokens, authenticated or signed URLs, session IDs, personal tracking IDs, and local paths. For URL fields use verified public posting or source URLs, dropping fragments, userinfo, and query parameters except source-specific public job identifiers. Also check paths for embedded secrets. Existing ATS normalization alone is insufficient for this projection.

- `public`: export metadata. Description text stays out of the bundle. A digest may be exported for a shareable description or capture. Use `null` for `raw_locator` or use an opaque `sha256:<digest>` reference to a shareable public capture. A purged capture keeps any safe `raw_sha`, with `raw_purged: true` on affected sightings and `raw_locator: null`.
- `private_email`: export only safe extracted posting metadata and the bookkeeping fields needed to link it. Use an opaque channel locator such as `urn:prospect:channel:<channel_id>`. Export `null` for the poll and sighting capture hashes and the sighting description hash and raw locator. Omit private message identifiers, recipient details, and mailbox or subscription links. Hash configuration only after projecting out credentials.
- If a required posting URL has no safe public equivalent, use `https://redacted.invalid/postings/<posting_id>` in both URL fields. The resulting URL-based identity key is still unique for the posting. The placeholder is a marker and must be excluded from matching and fetching. Replace unsafe required display text with `[redacted]` when no safe text is available, and use `null` for unsafe nullable metadata. Keep the sighting and its stable record links.
- `licensed` and `restricted`: exclude source-specific rows and content from the exchange under §4.2. A restricted skip recorded locally stays local. Raw captures, email bodies, SQLite files, and real snapshots stay outside Git. Exchange approved live snapshots through a controlled handoff.

### Reproducible snapshots

The golden fixture fixes all inputs, including entity and snapshot IDs, timestamps, classifier versions, and code/config hashes. The future fixture-store export must reproduce all eight files byte for byte. For deterministic writing, use UTF-8 without BOM, compact JSON with keys sorted lexically, literal Unicode, and LF line endings. Terminate each object with LF. Keep input array ordering stable, sorting `run_ids` by ID and aliases by `(kind, value)`. Read a consistent store view for the export. Consumers validate the received bytes against the manifest and need no reserialization for hash checks.

For live data, the same frozen store view, selection, and exporter settings yield identical data files and file hashes. Byte-for-byte reproduction of the full bundle also requires the original manifest inputs, including `snapshot_id` and `created_at`. Separately generated snapshots receive their own IDs and creation times. A new collection, changed labels, a purge, or a changed code/configuration can change the records or metadata even for the same window. Compare stable record IDs within a store lineage and check each snapshot against its own manifest.

### Sealed evaluation samples

1. The evaluation coordinator draws the sample, freezes the assignment manifest in evaluation-side storage, and commits its SHA-256 digest in Prospect's decision log before the scored export is made or anyone inspects results for that round. Prospect receives the digest and non-revealing coordination metadata, such as the round and window.
2. Give each reference collector their assigned employers and the codebook/procedure before enumeration. They can know what to search. Keep Prospect's sightings, labels, and match results inaccessible to them while they enumerate.
3. Freeze the reference evidence before comparison. Verify the frozen sample against the committed digest. Authorize the evaluator to pair the reference evidence with a snapshot whose `created_at` is after the seal time. The snapshot date check in §8.1 governs access for comparison. Any disclosure to the reference collector happens only after the reference evidence is frozen and as required for authorized adjudication.

### Implementation handoff

- **Codex (M1a, discovery).** Implement the producer against this record. Seed a temporary local SQLite store from synthetic inputs and reproduce the golden bundle byte for byte with a fixed clock and IDs. Write exports outside Git. The current hosted workflow uses a runner-local database and expiring artifacts. Local SQLite tests work on the developer machine or in a runner's own temporary filesystem. GitHub Actions and cloud agents consume a deliberately transferred bundle and have no assumed access to the product owner's Mac. The persistent live storage and execution choice belongs to M0.2 before the live run.
- **Claude Code (M2a, evaluation).** Mirror this decision and the golden bundle in the evaluation repository. Validate version, file hashes and counts, required fields, IDs, links, and stability when given a previous snapshot from the same lineage. Generate the six invalid cases below from the golden bundle. Build the sampler and the evaluation-only reference CSV intake against §8.1 and §5.3. In M2b, use independent normalization on the two sides' URLs.

The golden bundle contains 20 postings and 22 sightings across two runs. It exercises repeat sightings, an employer merge, a recorded closure, failed and partial polls, a skipped poll, a purged blob, missing employer resolution, out-of-scope and uncertain geography, label history, and private-email metadata with both a public URL and a redacted placeholder. All names and data are synthetic.

#### Six validator rejection cases for M2a

Start each case from a fresh copy. For cases that edit data files, regenerate their manifest row counts and hashes so the intended validation check runs.

| Case | Mutation |
| --- | --- |
| Unsupported version | Change `manifest.json`'s `contract_version` to `prospect-export/2`. |
| Checksum mismatch | Replace the recorded `runs.jsonl` SHA-256 digest with 64 zeroes. |
| Row count mismatch | Increase the recorded `channels.jsonl` row count by one. |
| Missing required field | Delete `declared_total` from the first poll. |
| Dangling reference | Set the first sighting's `poll_id` to `poll-missing`. |
| Stable ID drift | Use the golden bundle as the previous snapshot. In a copy with a later `created_at` and new snapshot ULID, change the posting with `identity_key` `greenhouse:civic-lantern:1020` from `posting-020` to `posting-021` and update its sighting reference. The resulting bundle is internally consistent but changes the ID of the same posting. |

M0.2 will resolve live deployment and export transport. M0.3 will resolve the live scope labels and rules. The evidence partition (M0.5) and production private-email parsing and redaction (M4) follow their own milestones.
