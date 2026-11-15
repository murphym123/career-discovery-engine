# Import public ATS board catalogs

The opt-in catalog CLI records employer board candidates in a private Prospect development store. Import, validation, and registry publication are separate commands. Each works across the full imported collection, with per-object byte limits for artifacts and adjustable run budgets for validation.

## Sources

| Catalog | Snapshot reported during source review | License and attribution |
| --- | --- | --- |
| LastRound AI ATS Company Directory, August 2026 CSV | 9,935 rows, including 4,966 Greenhouse, 2,856 Ashby and 2,113 Lever. The reported crawl dates are in July–August 2026. | Data by LastRound AI. Prospect normalizes and deduplicates observations. [LastRound AI study](https://lastroundai.com/blog/salary-transparency-study-2026) · [Directory documentation](https://github.com/fyrosofttech/lastroundai-hiring-data/blob/main/ats-directory/README.md) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| moonie0201 ATS Directory, `companies.jsonl` | 1,804 rows, including 1,442 with `ok` status across Greenhouse, Lever and Ashby. Some Lever observations use the EU endpoint. Recruitee and Rippling are retained for connector follow-up. | [moonie0201 ATS Directory and blocklist](https://github.com/moonie0201/ats-directory) · [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |

These totals describe the reported upstream snapshots. The reviewed `mherzog4/job-boards` repository Git-ignores its generated `boards.json`. Prospect uses the two catalogs above. [Upstream `job-boards`](https://github.com/mherzog4/job-boards)

## Pin both catalogs

Create `catalog-manifest.json` in a private directory outside Git. For a controller-run GitHub raw import, replace the two placeholders with full lowercase 40-character commit SHAs from the respective repositories. The moonie commit pins both the data and its blocklist. Optional `sha256` and `blocklist_sha256` fields additionally verify bytes. With the default revision `main`, those digest fields are required instead.

```json
{
  "last-round-ai": {"revision": "<LastRound AI repository commit>"},
  "moonie": {"revision": "<moonie0201 repository commit>"}
}
```

For files already staged by the controller, use this shape. Compute SHA-256 on the exact validated files, for example with `shasum -a 256`. Relative paths resolve from the manifest directory. An optional `revision` labels the known upstream commit when the bytes came from that commit. The stored metadata identifies a local-file import separately from a GitHub raw download.

```json
{
  "last-round-ai": {
    "file": "lastroundai-ats-company-directory-2026-08.csv",
    "sha256": "<CSV SHA-256>"
  },
  "moonie": {
    "file": "companies.jsonl",
    "sha256": "<JSONL SHA-256>",
    "blocklist_file": "blocklist.txt",
    "blocklist_sha256": "<blocklist SHA-256>"
  }
}
```

Either source may be selected individually. The optional top-level `"policy": {"file": "opt-outs.txt", "sha256": "<SHA-256>"}` applies local takedowns. Both blocklists accept one `provider:slug`, `provider/slug`, `provider,slug`, hosted board URL or bare slug per line, with `#` comments. A bare slug covers all catalog providers and `lever:slug` covers both Lever regions. A hosted Lever URL or `lever_eu:slug` selects its explicit region. Invalid policy entries abort the import. Withdrawals apply across both sources and future imports.

## Import, inspect, validate, and publish

```sh
PROSPECT_DATA_DIR="$HOME/Library/Application Support/Prospect/catalog-development"
PROSPECT_DISCOVERY_CONTACT='operator@example.org'  # Replace with a public contact.

# Use this import with locally staged objects.
python -m tools.import_board_catalog --data-dir "$PROSPECT_DATA_DIR" import \
  --manifest "$PROSPECT_DATA_DIR/catalog-manifest.json" --offline

# Or allow only the pinned public GitHub raw artifacts in the manifest.
python -m tools.import_board_catalog --data-dir "$PROSPECT_DATA_DIR" import \
  --manifest "$PROSPECT_DATA_DIR/catalog-manifest.json" --contact "$PROSPECT_DISCOVERY_CONTACT"

python -m tools.import_board_catalog --data-dir "$PROSPECT_DATA_DIR" status

python -m tools.import_board_catalog --data-dir "$PROSPECT_DATA_DIR" validate \
  --contact "$PROSPECT_DISCOVERY_CONTACT" --max-requests 20000 --concurrency 8 \
  --max-seconds 14400 --max-bytes 1000000000 --response-bytes 4000000 \
  --idle-seconds 60

python -m tools.import_board_catalog --data-dir "$PROSPECT_DATA_DIR" publish
```

The remote importer uses only the two built-in repository paths, moonie's blocklist and the raw host's robots file. Requests are anonymous, without redirects, and observe applicable robots rules, cooldowns and access stops. If a source route is unavailable, a controller with lawful access to separately obtained pinned objects can use local intake. Limits apply to each body (32 MiB for a dataset, 1 MiB for a blocklist and 512,000 bytes for robots). File hashes and policy are checked before the import transaction writes source evidence. The store retains metadata and digests without saving dataset or posting response bodies.

`status` reports object hashes, licenses, counts and rejections at import, current board outcomes, and origin holds. `effective_catalog_status` counts candidates, dead identities, and unsupported identities across the latest observation batches per source and board. `blocked_identities` and `withdrawals` report takedowns and any rejected withdrawal metadata. A validated provider and slug still record a withdrawal when an optional field is invalid. Add `--unsupported` to see current Recruitee and Rippling observations.

A LastRound row or a moonie `ok` or `unconfirmed` row can add a candidate. A newly imported `dead` row supersedes that source’s older candidate for the same identity. Older observations remain in the evidence history, and a positive observation from another source keeps the identity eligible. Omission from a snapshot leaves that source’s latest observation in place. Reimporting the same pinned data, including with a new blocklist, leaves its order unchanged. A new positive data snapshot can reopen a route after a 404 or 410. Prior successful Prospect HTTP verification remains available for the same board key.

Case variants share a normalized provider/slug key and keep separate spellings for validation. Lever US and EU have distinct keys. An explicit region or a matching hosted HTTPS board URL, including moonie’s `site` field, determines the route. Conflicting or malformed supplied board URLs are rejected. Publication retains bounded provenance paths and removes URL queries, fragments, and credential-bearing references.

Validation uses the [discovery scheduler](prospect-board-discovery.md) and limits probes to catalog candidates. It never queries an archive or scan index. Repeat the same command to resume and adjust request, time, byte, worker and origin budgets with the existing flags. `--max-requests` counts robots and board attempts. Keep the same applicable `--deny-prefix` flags on resume. The configured start interval and applicable robots pacing determine throughput on each origin. Inspect holds and deferred or failed statuses before resuming.

Publication merges verified boards into `ats-registry.json` in the data directory. To use an existing registry, pass its path to `publish --registry` and pause other registry writers during the merge. Publication keeps existing poll state and attaches catalog provenance, source and license links, file hashes and exact source record references. Retain these fields when sharing derived catalog data.

Publish after a validation run records an access stop so the polling registry receives the hold. A stopped provider origin suppresses new entries for that origin and blocks selection of cached entries while retaining their prior verification. A timed cooldown expires at its recorded deadline. Republishing after a persistent stop has been cleared in the discovery store updates the registry’s hold. Catalog-only entries whose latest observations are all dead remain in existing registries for audit and become eligible again when a source provides a current candidate. New takedowns suppress future discovery probes and registry selection, remove withdrawn catalog proofs from the registry, and preserve historical poll fields.
