# Prospect M1a collection store and snapshot export

Prospect keeps a history of what USAJOBS, Greenhouse, Lever, and JazzHR returned. A job keeps
the same record ID when it appears again, and each appearance records where and
when Prospect saw it. The exported snapshot gives evaluation a checkable copy of
that evidence. It includes unsuccessful and incomplete searches so a missing job
can be interpreted in the context of what Prospect actually searched.

## Local storage

Set `PROSPECT_DATA_DIR` to a directory outside Git to opt the existing USAJOBS,
Greenhouse, Lever, and JazzHR collection paths into persistence. The setting works with
direct structured collection, the regional USAJOBS handoff, and calls to the ATS
board collector for those providers. Collection requests, budgets, ranking and
report deduplication use their existing paths. The adapter observes rows before
report deduplication.
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
captures, ordered inventory receipts, postings, sightings, and append-only labels. Frames have storage for
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
is refused. The store records which inventory responses were retained or
withheld and the number of sightings each supplied. The withheld body and its
digest are absent. Counts of unrepresentable records are recorded at intake, independently
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

### Lever

The M1c adapter uses the existing public U.S. and European Lever endpoints and
their existing page and request limits. Each poll records every returned row,
including repeated occurrences. Its ordered receipts point to the exact retained
inventory pages. The optional public board title uses the existing lookup and
has a separate receipt when captured. A board with a valid inventory gets a
provisional employer keyed to its channel unless the channel already has an
employer. The public title, when safe and available, supplies the display name.
Otherwise the tenant supplies it. The same display name can belong to multiple
tenants. Native posting IDs are scoped to the tenant and Lever region.

A completed empty inventory is `ok` with zero records. A transport or schema
failure before any valid inventory page is `failed`. A poll with a valid page
is `partial` if collection stops early or any rows are malformed. Identifiable
rows are retained and unidentifiable rows are counted under decision 0003.
Lever does not declare a total in these responses. A job missing from a later
poll retains its previous observation history. Prospect derives no publication
or closure date from the inventory.

Public discovery channels use the existing retention and snapshot rules.
An existing channel with a different retention class or evidence role is refused
before requesting inventory. The adapter imports no historical or evaluation
evidence. A response that echoes a configured credential is withheld. The snapshot
records `secret_echo` for an affected inventory poll, even when another page was
retained. The legacy report also omits the withheld response's digest and
redacts the echoed credential. URLs and display fields are projected to shareable
values at intake.
The snapshot contains metadata and eligible digests under the unchanged 1.1
contract. Exact public captures stay under `PROSPECT_DATA_DIR`.

The adapter keeps the reported location options and puts a leading `Remote`
label on an explicitly remote posting. Geography replay can use that designation
even when the raw capture was withheld or purged. Any stated US or DC
eligibility remains subject to the existing gazetteer rules. M0.2 deployment,
M0.3/M0.4 geography and scope policy, and M0.5 evidence partitioning follow
their own decision points.

To exercise Lever with synthetic responses:

```sh
python -m unittest discover -s tests -p 'test_prospect_lever.py' -v
```

Set `PROSPECT_EVAL_CHECKOUT` to a local evaluator checkout to also validate
the produced Lever 1.1 bundle with the independent validator.

### JazzHR

The JazzHR adapter observes the existing robots-aware public employer board and
the existing per-board detail requests. It uses the same request budgets. Sitemap
discovery stays a source of board URLs, and the shared post-discovery enrichment
continues to serve the legacy report.

Each identifiable index card becomes a sighting, including duplicates and cards
beyond the legacy display cap. The board tenant and the case-preserving JazzHR ID
identify the posting. An identifiable card with no usable link keeps its identity
and gets the contract's redacted URL. A card that cannot be identified contributes
to `unrepresented`. The index declares no total, and a completed poll describes
the visible page. A positive empty notice on a recognized career page supports
an `ok` zero-result poll only when visible same-board job links or unparsed job
cards do not contradict it. Missing or unreadable markup is a failure. An indicated
continuation, an unrepresentable card, or a failed attempted detail request can
make an otherwise usable poll partial. Ordinary legacy output keeps its existing
rows, caps, and empty-page status.

The index digest stays attached to each sighting. Accepted detail HTML gets a
separate local receipt keyed to the native ID, and its description digest and
dates go on the occurrence selected for inspection. Geography replay uses that
receipt's structured worksites and explicit remote arrangement alongside the
original index location. Purged or unavailable details leave the index location
available for replay. A changed job URL or a different posting's metadata cannot
enrich a sighting.

Challenge pages and recognizable private application state are refused before
storage. If a detail response is refused, the index evidence remains and the
poll records the incomplete attempt. An inventory response that echoes a
configured credential follows decision 0003. A detail echo cannot stand in for
missing inventory evidence. The snapshot exchanges safe metadata and digests
under the existing 1.1 contract. The offline fixtures exercise the supported
markup and failure signals. Other templates and live access remain unverified.

```sh
python -m unittest discover -s tests -p 'test_prospect_jazzhr.py' -v
```

Set `PROSPECT_EVAL_CHECKOUT` to exercise the JazzHR snapshot against the
independent evaluator.

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
snapshots. It also refuses unexplained missing inventory responses and counts
that do not reconcile.

The exporter reads a consistent store view and writes the seven ordered JSONL
files and the JSON manifest from the
[approved export contract](decisions/0001-export-contract.md). The default is
`prospect-export/1.1`. Each poll discloses known capture withholding and counts
unrepresentable source records. Use `--contract-version prospect-export/1` for
historical v1 output when the selected polls can be expressed without those
disclosures. The manifest
carries the count and SHA-256 of each file. The projection excludes raw bodies,
descriptions, credentials, mailbox locators, and restricted or licensed
source rows. It strips tracking parameters and fragments, preserves recognized
public job identifiers, and uses the contract's redacted URL when it lacks a
shareable link. Snapshot IDs and creation times are fresh for normal exports.
With fixed manifest inputs, the offline tests reproduce both eight-file golden
bundles byte for byte.

```sh
python -m unittest discover -s tests -p 'test_prospect_store.py' -v
python -m unittest discover -s tests
```

The fixture test seeds a temporary SQLite store with the approved synthetic
records, reconstructs their posting lifecycles, recreates the synthetic public
bodies, and purges the designated body before exporting. It compares each
resulting file with the shared fixture. Collector tests use in-memory responses
and no live credentials. The separate
[1.1 golden bundle](../fixtures/export-v1.1-example/) is generated from the real
adapters with synthetic responses and covers a withheld-only poll, a mixed
safe/withheld poll, and a poll with an unrepresentable row.

To check the [approved poll accounting rules](decisions/0003-poll-evidence-accounting.md)
across repositories, run the cross-repository test with the M2a validator available locally:

```sh
PROSPECT_EVAL_CHECKOUT=/path/to/prospect-evaluation \
  python -m unittest discover -s tests -p 'test_prospect_store.py' -v
```

The test uses the evaluator's CLI against actual synthetic collector exports.
Use the evaluator revision that supports 1.1 by default. The public CI suite
runs the producer tests and skips this one external check when the private
evaluation checkout is unavailable.

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
