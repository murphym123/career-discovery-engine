# 0002 — Proposed poll evidence accounting

**Status:** Proposed. Product-owner approval is required before adoption.
**Would amend:** [0001, the approved export contract](0001-export-contract.md), from `prospect-export/1` to `prospect-export/1.1`.
**Review:** [M1a producer PR #34](https://github.com/murphym123/career-discovery-engine/pull/34) and the [M2a draft and validator](https://github.com/murphym123/prospect-evaluation/pull/1).

A public API response can supply usable posting metadata while echoing a credential in its body. Prospect must withhold the body and its digest. A provider can also supply a row with too little evidence for a posting identity. That row contributes to the number returned, but attaching it to an invented job would corrupt evaluation. The validator needs a way to distinguish both events from unexplained losses.

## Proposed wire rules

Only `polls.jsonl` gains fields. Both keys are required in the draft format.

| Field | Type | Meaning |
| --- | --- | --- |
| `capture_withheld` | String or null | `secret_echo` if the missing primary public inventory capture is explained by a response echoing a configured request credential. Otherwise null. The reason is a fixed code. |
| `unrepresented` | Nonnegative integer | Source records explicitly counted at the collector boundary that lacked sufficient identity evidence to create a sighting. Use zero when all rows were represented. |

- An `ok` or `partial` public poll with `received > 0` requires either `raw_sha` or an allowed `capture_withheld` code. A withholding code requires a public channel and a null `raw_sha`. It does not substitute for a body that went missing for an unknown reason. It does not turn a collection failure into a success.
- If a multi-page poll retains a safe inventory response, its hash remains the poll hash. The local store records the number of withheld inventory responses, and sightings from withheld pages have null raw capture fields. The poll-level withholding code is reserved for the case where the primary hash is absent. Prospect sets the code only when it has evidence of suppression and no unexplained absent record-bearing response.
- For every poll, the number of exported sightings plus `unrepresented` must equal `received`. The producer counts unrepresentable rows individually at intake. It does not compute the count from a missing-sighting difference. A positive count requires `partial` or `failed` status. Retain `received`, all identifiable occurrences, and any safe raw evidence. An unexplained difference fails export and validation.
- No withheld bytes, digests of withheld bodies, credential values, provider error text, or extra source-row content are sent under these fields. Existing capture and privacy rules still apply.

## Approval and compatibility

The approved `prospect-export/1` path and its golden bundle remain byte-for-byte compatible. An affected poll that v1 cannot express causes the producer to fail the export without publishing a partial bundle. Local polls, postings and sightings remain available. Earlier local M1a counts inferred from a difference are preserved as history and cannot serve as verified accounting in a draft export.

For synthetic cross-repository testing, Prospect can explicitly emit `prospect-export/1.1` with `--draft-1-1`. The evaluation validator accepts that version only with `--allow-proposed`. The evaluation scoring gate continues to use approved versions only. The v1 golden fixture stays in place. If the owner approves, coordinate activation and a separate 1.1 fixture with the evaluation repository. The 1.1 identifier leaves the v1 test case that rejects `prospect-export/2` intact.

The cross-repository test generates both cases through the producer's collector adapters. It verifies draft acceptance, default rejection of the unapproved version, rejection of the cases in v1, and rejection of a missing capture explanation or an extra dropped sighting in the draft.
