# Prospect M1a collection store and snapshot export

Prospect now keeps a history of what USAJOBS and Greenhouse returned. A job keeps
the same record ID when it appears again, and each appearance records where and
when Prospect saw it. The exported snapshot gives evaluation a checkable copy of
that evidence. It includes unsuccessful and incomplete searches so a missing job
can be interpreted in the context of what Prospect actually searched.

## Local storage

Set `PROSPECT_DATA_DIR` to a directory outside Git to opt the existing USAJOBS
and Greenhouse collection paths into persistence. The setting works with direct
structured collection, the regional USAJOBS handoff, and calls to the Greenhouse
board collector. Collection requests, budgets, ranking and report deduplication
use their existing paths. The adapter observes rows before report deduplication.
It also retains identifiable Greenhouse postings from the fetched inventory
beyond the report cap. This adds no requests.

Each opt-in collector invocation gets a run and a poll for its channel. A caller
using the Python adapter functions in `career_engine.store.collectors` can pass
a single open run to multiple polls. USAJOBS records missing credentials or
unconfirmed terms as skips. Greenhouse records a skip when an existing transport
budget or host stop prevents an attempted poll. Work that an outer workflow
never hands to a collector remains in that workflow's own status report.

| Path under `PROSPECT_DATA_DIR` | Contents |
| --- | --- |
| `prospect.sqlite` | Version 1 SQLite database in WAL mode. The accompanying WAL and shared-memory files are managed by SQLite. |
| `blobs/public/<first two hex digits>/<sha256>.gz` | Exact, fully consumed public response bodies, compressed and keyed by their SHA-256 digest. |
| `backups/` | SQLite online backups made after runs, keeping the latest 14 and the latest backup in each older month. |
| `exports/<snapshot_id>/` | Completed exchange bundles when using the default export destination. |

The SQL migration in `career_engine/store/schema.sql` defines employers and
aliases, employer merges, frames and memberships, channels, runs, polls,
captures, postings, sightings, and append-only labels. Frames have storage for
later work. A posting is identified by its provider, tenant and native job ID,
or by its URL when a native ID is unavailable. Employer references remain
nullable until there is evidence to resolve them. The store does not infer
closure from disappearance. Closure can be recorded as a separate observation.

A poll and its sightings commit together. The poll keeps the primary response
digest and ordered receipts for additional responses. Each sighting points to
its inventory page. The local poll also counts provider rows that could not be
represented as identifiable postings. Public raw bodies retain the original
evidence for those rows. Denials, interrupted streams, and response bodies the
existing collector does not read are represented by poll status and available
counts. The adapter never reads extra bytes to obtain a capture. USAJOBS request
headers are never stored, and a body that echoes a configured USAJOBS credential
is refused. The store records the withholding event without storing or hashing
that body. Counts of unrepresentable records are recorded at intake, independently
of how many sightings were written. Offline tests use synthetic responses and
credentials.

The blob store refuses restricted and licensed raw captures and fails closed for
private email until M4 provides sanitization. Public bodies are pruned after 18
months of retention by default. A later capture of identical content renews its
retention age without changing its digest. Removing a body leaves its database
history and digest intact. An export marks a sighting `raw_purged` whenever its
recorded public body is absent, including a body missing from a restored backup.
Keep the SQLite backups and public blobs together if the ability to replay
original bodies matters.

## Export and reproduce

From a checkout with a completed store:

```sh
python -m career_engine.export \
  --data-dir /path/outside/git/prospect \
  --out /path/outside/git/prospect/exports
```

`--data-dir` defaults to `PROSPECT_DATA_DIR`. `--out` is the parent of the new
snapshot directory and defaults to `<data-dir>/exports`. Use `--from` and
`--to` for the inclusive start and exclusive end of the poll window. The
default window starts at the Unix epoch and ends at snapshot creation. The
exporter prints the path it created. Set `--code-sha` or `PROSPECT_CODE_SHA`
when running outside a Git checkout. If a selected poll belongs to an unfinished
run, the exporter fails clearly and leaves the destination free of partial
snapshots. It also refuses unexplained missing primary inventory captures, counts that
do not reconcile, and polls whose accounting requires an unapproved version.

The exporter reads a consistent store view and writes the seven ordered JSONL
files and the JSON manifest from the
[approved export contract](decisions/0001-export-contract.md). The manifest
carries the count and SHA-256 of each file. The projection excludes raw bodies,
descriptions, credentials, mailbox locators, and restricted or licensed
source rows. It strips tracking parameters and fragments, preserves recognized
public job identifiers, and uses the contract's redacted URL when it lacks a
shareable link. Snapshot IDs and creation times are fresh for normal exports.
With fixed manifest inputs, the offline fixture test reproduces all eight
golden files byte for byte.

```sh
python -m unittest discover -s tests -p 'test_prospect_store.py' -v
python -m unittest discover -s tests
```

The fixture test seeds a temporary SQLite store with the approved synthetic
records, reconstructs their posting lifecycles, recreates the synthetic public
bodies, and purges the designated body before exporting. It compares each
resulting file with the shared fixture. Collector tests use in-memory responses
and no live credentials.

For the two cases in the [proposed poll accounting decision](decisions/0002-poll-evidence-accounting.md),
run the cross-repository test with the M2a validator available locally:

```sh
PROSPECT_EVAL_CHECKOUT=/path/to/prospect-evaluation \
  python -m unittest discover -s tests -p 'test_prospect_store.py' -v
```

The test uses the evaluator's CLI against actual synthetic collector exports.
The review tested evaluator commit `1ff14f189ac78b034eb5aed2f4ded23f156e2fe9`.
The public CI suite runs the producer tests and skips this one external check
when the private evaluation checkout is unavailable. `--draft-1-1` generates the
proposed format for compatibility testing. Production exports still use v1.

## Open operational and privacy decisions

M0.2 owns the persistent live execution, storage, and transfer choices. This
implementation uses a local filesystem and POSIX file locks on the Mac or a
runner's own filesystem. A cloud runner has access to files explicitly handed
to it. M1a has no live deployment or schedule.

URL checks operate on the supplied public evidence and known provider formats
without fetching each link. A source can place an unlabeled secret in a
public-looking path or text field, so source-specific review is still needed
before adding a new connector or sharing real snapshots. Private-email
ingestion and sanitization remain M4 work.
