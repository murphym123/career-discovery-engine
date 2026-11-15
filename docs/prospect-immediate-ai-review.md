# Offline review of saved Prospect snapshots

Run from the repository root with Python 3.10 or later and the repository dependencies installed.

```sh
python3 -m tools.export_immediate_review \
  --input /path/to/first-saved-snapshot.json \
  --input /path/to/second-saved-snapshot.json \
  --output-dir /path/to/private-output \
  --max-records 50
```

Each input is a UTF-8 JSON array of board results with a `jobs` array and optional board, provider, status, name, and errors. Missing and blank optional text becomes null, with whitespace-only descriptions preserved and counted separately. Invalid input aborts the export before any bundle is written. The output root must be outside Git and owned by the operator, with no group or other permissions. The CLI can create the root if its parent exists.

The command only exports data. It takes no applicant dossier. Stdout reports counts and digests. The bundle contains private files with original captured description text. Keep the bundle local and out of version control.

## Bundle

- `manifest.json` is the inventory for `prospect-immediate-review/1`. It gives counts, source-file SHA-256 digests, board statuses, error counts and error digests, missing fields, omitted link counts, and checksums and row counts for every JSONL file. An observation position is a zero-based `[input_index, board_index, job_index]` into this inventory and the original arrays. The source file hash identifies the exact snapshot bytes. Raw error messages and file paths stay in their original snapshots.
- `records.jsonl` contains one record per provisional provider, board, and native source ID. Provider and board fall back to the enclosing board result when the job lacks them. Rows without a complete native key each get a separate record. The exporter makes no cross-board matches and applies no relevance, geography, employer, or completeness filter.
- Every observation appears in exactly one record's `variants[].observations`. Identical projected data within a record shares one variant. Disagreements remain as separate variants with their own observations. The preferred variant favors a present description flagged complete, then one with unknown truncation status, then an explicitly truncated one. Within a tier, longer captured text comes first, with the variant digest breaking ties. All descriptions and dates are saved evidence. Availability is unverified for every record.
- The two posting link fields retain the original string if it passes the repository's public-URL checks and all query parameters are permitted public identifiers. A rejected link becomes null and its field name appears under `omitted_urls`. The manifest counts these separately from missing links. Both structured links and embedded references remain unverified. Description text is an inert string and can contain raw HTML and embedded links.
- Every `batch-NNNNNN.jsonl` is a consecutive slice of the corpus sorted by review ID. The cap applies to records per file. Text and variants have no size cap. Concatenating batches in manifest order reproduces the corpus exactly. An empty corpus has no batches.
- Canonical object digests use UTF-8 compact JSON, sorted keys, literal Unicode, and one terminal LF. A record digest excludes its own `record_sha256`. A variant digest covers only its `data`. An observation digest covers its original JSON object, including unprojected fields. `description_sha256` is the collector's supplied hash. `description_text_sha256` hashes the retained text's exact UTF-8 bytes.
- Inputs are ordered by source digest. With the same input bytes, batch size, exporter version, and configured-secret projection, the manifest and corpus reproduce byte for byte. A bundle's directory name incorporates the manifest's exact SHA-256. Reruns verify the existing bundle and leave its files untouched. Changed settings create a separate bundle. No timestamp or absolute input path enters the output.

The [versioned assessment rubric and response template](prospect-ai-assessment-v1.md) define a future review. Check actual byte or token budgets before routing a batch to any model. A record-count cap alone does not bound model context.
