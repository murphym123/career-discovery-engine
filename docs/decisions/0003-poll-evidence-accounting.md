# 0003 — Poll evidence accounting

**Status:** Approved by the product owner on November 11, 2026, with the multi-page rules below. [Product decision on PR #34](https://github.com/murphym123/career-discovery-engine/pull/34#issuecomment-6813396617).

This decision amends the [export contract](0001-export-contract.md) to `prospect-export/1.1`. Prospect uses 1.1 by default. The historical `prospect-export/1` format, its [golden bundle](../../fixtures/export-v1-example/), and its six rejection cases remain available.

A public API can return usable job records while echoing a credential in the inventory response. Prospect withholds that body and its digest. A provider can also return a row with too little evidence to identify a posting. Prospect counts that row without inventing a posting identity.

## Wire rules

Only `polls.jsonl` gains fields. Both keys are required in 1.1.

| Field | Type | Meaning |
| --- | --- | --- |
| `capture_withheld` | Null or the fixed string `secret_echo` | At least one public inventory response in the poll was deliberately withheld because it echoed a configured request credential. Null means there was no recorded withholding. |
| `unrepresented` | Nonnegative integer | Source records individually counted at intake that lacked sufficient evidence to identify a posting. Use zero when all were represented. |

- A public poll can report both `raw_sha` and `capture_withheld`. The digest identifies the first retained inventory response. The withholding reason discloses an additional suppressed response and must be present even when a safe digest exists. If every inventory response was withheld, `raw_sha` is null.
- An `ok` or `partial` public poll with `received > 0` requires a retained inventory digest or a recorded withholding. A public sighting with a null `raw_sha` must come from a deliberately withheld response and its poll must disclose that withholding. Purged captures retain their original digest.
- The producer keeps ordered local inventory receipts and reconciles sighting counts by capture digest, including sightings with deliberately withheld captures. A withholding code cannot explain an unrelated missing inventory response or a lost sighting capture. An unexplained gap blocks export. A withheld body and its digest are neither persisted nor exported. Its original body cannot be replayed.
- For every poll, exported sightings plus `unrepresented` must equal `received`. A positive `unrepresented` requires `partial` or `failed` status. The producer counts these rows at intake and retains every identifiable occurrence. A missing sighting does not increment this count.
- The new fields contain only the fixed code and aggregate count. They do not carry credentials, raw record text, or error messages.

The separate [1.1 golden bundle](../../fixtures/export-v1.1-example/) comes from the real collector adapters using deterministic synthetic inputs. It covers a withheld-only poll, a multi-page poll with a safe and a withheld response, and an unrepresentable source row. Historical v1 can be selected explicitly for windows whose evidence fits v1. The producer refuses a legacy export that would omit known withholding or unrepresentable records.

The evaluation repository mirrors this decision and both fixtures, validates both versions, and reports reduced replayability when a capture was withheld. Both CI workflows and the actual producer-to-validator check must pass before either PR merges. [M2a evaluator PR #1](https://github.com/murphym123/prospect-evaluation/pull/1).
