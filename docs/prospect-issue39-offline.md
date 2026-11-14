# Issue #39 pilot transport and execution gate

The [pilot specification and owner decision](https://github.com/murphym123/career-discovery-engine/issues/39#issuecomment-6870492680) authorize offline preparation. Before running `collect`, obtain independent review and passing CI on merged `main`, then separate explicit owner approval for the pinned code, exact configuration digest, cohort, endpoint terms and retention, and one pass. The approval digest argument ties the command to those bytes. Owner approval itself is recorded outside the program.

On the owner's Mac, confirm local encryption, an owner-only parent directory outside Git and synced storage, a reviewed Python environment, at least 2 GiB free, an encrypted backup destination with a tested restore, and the evaluation custodian's clearance for development exposure. Keep the input files, run root, database, captures, backups and exports private. All pilot evidence is development evidence. The repeat allocation is disabled.

## Offline verification

From the producer root, set `EVAL` to the code-only evaluator at revision `ec8b96825e2b4770e90c0f360af1582d2d2c72c5`. Use Python 3.10 and 3.13 environments with the current producer requirements. `RUN8` is optional and points to the original local Run 8 ZIP for historical geography replay.

```sh
PROSPECT_EVAL_CHECKOUT="$EVAL" PROSPECT_RUN8_ARCHIVE="${RUN8:-}" \
  python3.10 -m unittest discover -s tests -v
PROSPECT_EVAL_CHECKOUT="$EVAL" PROSPECT_RUN8_ARCHIVE="${RUN8:-}" \
  python3.13 -m unittest discover -s tests -v

PRODUCER="$PWD"
(cd "$EVAL" && python3.13 -m contract.validate "$PRODUCER/fixtures/export-v1.1-example" --json)
(cd "$EVAL" && python3.13 -m contract.validate "$PRODUCER/fixtures/export-v1-example" --json)
```

The suite exercises the unchanged collector entry points, frozen cold selection, interrupted partial polls, and offline export and validator integration. The live-wire tests stub the HTTP adapter and disable socket connections and DNS. Fixture replay reproduces all eight export files with the original manifest inputs.

## Private input and offline precheck

Write the approved canonical cohort URLs in the [24-board order in issue #39](https://github.com/murphym123/career-discovery-engine/issues/39#issuecomment-6870435519), one URL per line. The URL digest is checked independently of raw file formatting. Use owner-only regular files. The private JSON configuration has exactly these keys.

| Key | Value |
| --- | --- |
| `code_sha` | Full approved merged producer commit |
| `terms_get_attempts` | Operator count of all terms and documentation GET attempts for this authorization, including redirects and failures, from 0 to 16 |
| `terms` | Reviewed endpoint permissions. An omitted route is denied. |

Each permission includes `arm`, `host`, `path`, `documentation`, `terms`, `checked_at`, `reviewer`, `disposition` and `retention`. Use canonical public HTTPS references, a UTC timestamp, a reviewer label, and `disposition: allowed` only after reviewing the actual documented route, anonymous automation, and retention. Discovery permissions use `digest_only`. Board permissions use `public_18_months` only when the normal store and backup retention can be honored. Omit permissions for sources with stricter rules the pilot cannot meet. Record any excluded scopes and reasons in the private readiness record.

Seed permissions are exact host and path pairs. JazzHR detail uses `/apply/{posting}` for the approved host, with the actual posting path derived from the observed index and the fixed detail slots. Discovery uses `index.commoncrawl.org` with `/collinfo.json` and the reviewed `/CC-MAIN-{collection}-index` template (or exact index path), and `app.jazz.co` with each approved `/feeds/google/xml/0` or `/feeds/google/xml/1` path. A template matches only the collection observed in the current catalog. Cold permissions may cover the documented provider templates after review. The runner binds each to the run-discovered, selected tenant.

| Cold host | Cold path |
| --- | --- |
| `boards-api.greenhouse.io` | `/v1/boards/{tenant}` or `/v1/boards/{tenant}/jobs` |
| `api.lever.co` | `/v0/postings/{tenant}` |
| `jobs.lever.co` | `/{tenant}` |
| `{tenant}.applytojob.com` | `/apply` |

```sh
umask 077
python -m tools.pilot39 precheck --cohort "$COHORT" --config "$CONFIG"
```

## Command after explicit authorization

Use the exact command from the reviewed approval record, in a clean detached checkout of the approved merged revision. Point `PROSPECT_DATA_DIR` at a **new** path with an existing owner-only parent on the approved local volume. The runner creates the directory and freezes the input bytes before it sends requests. A staged directory or previous pilot directory cannot be reused.

```sh
umask 077
test "$(git rev-parse HEAD)" = "$APPROVED_CODE_SHA"
test -z "$(git status --porcelain)"
export PROSPECT_DATA_DIR="$APPROVED_PRIVATE_RUN_PATH"
python -m tools.pilot39 collect --cohort "$COHORT" --config "$CONFIG" \
  --owner-approved-config-sha256 "$APPROVED_CONFIG_SHA256"
```

`run/` contains frozen inputs, provenance, the seed and cold cohort ledger, candidate discovery evidence, and fsynced operations records. Each dispatched GET has a persisted attempt before the socket call and a separate result. The board ledger records durable poll status, observed/represented counts, inventory receipts and capture disposition. The run manifest is published after both arms close, geography replay, a local SQLite backup, and the eight-file export. It records the window, counts, stop reason and snapshot manifest hash.

Check the resulting bundle with the pinned `contract.validate` CLI, require exit 0 and `ok: true`, and reconcile counts and raw evidence dispositions with the store and board ledger. Review exposed identifiers, incomplete inventory and the manual QA sample locally. After validation and review, remove snapshot write permissions and copy the verified SQLite online backup with its referenced blobs, sidecars and snapshot to the approved encrypted backup destination. Verify restore, integrity, foreign keys and decompressed capture digests before handoff.

For interruption or failure, keep the directory and journal. Run the offline inspection command after the writer has exited.

```sh
python -m tools.pilot39 inspect --data-dir "$PROSPECT_DATA_DIR"
```

An attempt without a result is counted conservatively as dispatched for incident review. The inspection reports committed polls missing the corresponding ledger entry, dispatches without a poll, unfinished arms, and a truncated journal tail. The program leaves interrupted or failed runs unfinished when relevant. There is no resume or automatic export of interrupted runs. Reconcile locally before any owner-approved recovery. The presence of an export directory alone is insufficient to establish a finalized run.

## Limits and residual risks

- Fixed stages allow up to 16 operator terms/docs, 10 discovery, 96 seeded and 26 cold attempts. The maximum is 160 including the unavailable 12-request repeat allocation. The active ceilings sum to 148. Stage, byte and pass deadlines stop further dispatch. A 75-second board deadline ends that board. Robots, failures and redirect hops consume the same stage allowance.
- The transport requests uncompressed responses, rejects other encodings, validates TLS, and applies a 5-second connect timeout, 10-second read timeout, and a 25-second elapsed alarm including headers and streaming. A 128 MiB body budget includes bounded reads of blocked/overflow responses where needed. A failed read can carry a conservative byte charge. An OS-level stall that delays delivery of the Unix alarm can delay the process. The operator interruption and storage procedures still apply.
- Terms review is operator-supplied. An unavailable, ambiguous or restrictive robots file, unapproved redirect, unsupported representation or conservative privacy heuristic can force a skip or stop. Privacy heuristics do not establish the absence of all sensitive content. Check captures on the approved machine before backup or sharing.
- The existing adapter semantics are retained. Missing approvals, incomplete pages, truncation and privacy stops can reduce the available diagnostic window. The selection and resulting export support operational analysis of observed sources.
