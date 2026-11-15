# Compact triage of saved Prospect jobs

From the repository root, prepare packets from an existing offline exporter bundle and the exact saved targeted snapshot used to build it.

```sh
python3 -m tools.prepare_compact_triage \
  --export-dir "$review_bundle" \
  --targeted-snapshot "$targeted_snapshot" \
  --output-dir "$triage"
```

The private output root is created with mode 700 and must be outside Git. The command prints the generated `compact-…` bundle name, counts by tier, and the first two targeted packet names. Within the bundle, `compact-records.jsonl` accounts for every input ID. A record is TARGETED if any observation came from the snapshot whose exact bytes match the targeted SHA-256. All other records are GENERAL. The manifest lists targeted packets first. Within a tier, packets rotate through source boards in a stable order. Defaults cap each packet at 50 jobs and each complete prompt at 65,536 UTF-8 bytes. Identical preparation reuses a checksum-verified bundle.

Each row has one excerpt marker pointing to the observation for the preferred variant. That observation can come from a general board snapshot even if another observation makes the record TARGETED. The row carries all contributing source file hashes. The snippet keeps up to 500 characters of the preferred description's opening plain text. A missing employer can fall back to the excerpt's board name and is flagged when it does. All availability is unverified. The source corpus retains full descriptions and provenance.

## Run and accept the two calibration packets

Set `triage` to the private output root and `bundle` to the full path of the prepared `compact-…` directory. With model use authorized, run the following from the repository root.

```sh
umask 077
for stem in targeted-000001 targeted-000002; do
  codex exec --ephemeral --sandbox read-only --skip-git-repo-check \
    -C "$triage" -o "$triage/$stem.response.json" - \
    < "$bundle/$stem.prompt.txt" > /dev/null || break
  python3 -m tools.accept_compact_triage \
    --packet "$bundle/$stem.json" --response "$triage/$stem.response.json" || break
done
```

The prompt file can also be submitted as-is to a model. Save its reply as raw JSON with a top-level `results` array. The accept command parses and validates the entire reply before recording any row. If validation fails, correct the response and rerun with the same packet. Accepting an identical set of results again is a replay. A conflicting result is rejected and leaves the previously accepted rows intact.

The checkpoint is a mode-600 SQLite file beside the bundle. Its `results` table has one row per `review_id`. The command reports counts by tier for classifications, actions for human review, and unprocessed rows. Query `review_path` for `shortlist_review` or `needs_evidence` and join to the compact corpus by `review_id` for follow-up. The `negative_audit` path is a stable hash sample of roughly 10% of unlikely results for later independent review. All results and original records remain available. Confirm current availability, location, and requirements on the employer or ATS posting before counting a listing toward a live shortlist.
