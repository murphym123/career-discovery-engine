# Shortlist file contract (`shortlist.json`, schema version 1)

The **Shortlist** view shows a small, curated set of jobs chosen by the
assessment workflow. The dashboard only displays this file. It does not
select, rank, score, or re-check jobs, and it never opens the discovery
database, collectors, raw captures, or an applicant dossier.

## Where the file is read from

1. `PROSPECT_SHORTLIST`, if set: the full path to the file.
2. Otherwise `$PROSPECT_DATA_DIR/shortlist.json`.

If neither exists, or `jobs` is empty, the view shows **No shortlist yet**.
It never substitutes placeholder jobs. An invalid file shows an error naming
the problem and no cards.

## Format

```json
{
  "schema_version": 1,
  "generated_at": "2026-11-15T00:00:00Z",
  "demo": false,
  "jobs": [
    {
      "review_id": "stable-id-from-assessment",
      "title": "Policy Analyst",
      "employer": "Employer name",
      "location": "Washington, DC",
      "work_arrangement": "Hybrid (3 days on site)",
      "compensation_text": "$60,000–$70,000 per year",
      "official_url": "https://employer-or-ats.example/jobs/123",
      "application_url": null,
      "fit_reasons": ["Evidence-based reason 1", "Reason 2"],
      "gaps_to_verify": ["Requirement or unknown worth checking"],
      "review_priority": "prioritize",
      "availability_status": "verified_current",
      "availability_checked_at": "2026-11-14T15:30:00-05:00",
      "listing": {
        "source": "targeted_snapshot", "provider": "greenhouse", "board_key": "employer-board",
        "retrieved_at": "2026-11-14T18:02:11Z", "source_id": "4100",
        "title": "Policy Analyst", "company": "Employer name", "location": "Washington, DC",
        "geography": null, "work_arrangement": "Hybrid", "employment_type": "Full-time",
        "category": null, "posted_at": "2026-11-01", "updated_at": null, "closing_at": null,
        "record_kind": "job", "detail_status": "fetched", "evidence_status": "public",
        "description": "Full saved description text…", "description_truncated": false,
        "description_sha256": null, "description_text_sha256": "<sha256>",
        "identity": null, "omitted_urls": [],
        "url": "https://boards.greenhouse.io/employer-board/jobs/4100",
        "original_posting_url": "https://employer-or-ats.example/jobs/123"
      }
    }
  ]
}
```

| Field | Required | Rules |
| --- | --- | --- |
| `schema_version` | yes | Must be `1`. Any other value is rejected. |
| `generated_at` | no | `YYYY-MM-DD` or a timezone-aware ISO timestamp. |
| `demo` | no | `true` shows a **DEMO SHORTLIST** banner and keeps feedback session-only. |
| `jobs` | yes | Array, at most 200 items. Order is preserved as the display order. |
| `review_id` | yes | Unique, `[A-Za-z0-9][A-Za-z0-9._-]*`. Same ID as the assessment output. |
| `title`, `employer`, `location`, `work_arrangement`, `compensation_text` | no | Plain text. Missing values show as "not stated". |
| `official_url` | no | Exact employer/ATS posting link. Must be public HTTPS. |
| `application_url` | no | Defaults to `official_url`. |
| `fit_reasons`, `gaps_to_verify` | no | Accepted for compatibility with #75 but not shown on cards. |
| `listing` | no | What Prospect extracted from the listing: the preferred variant's `data` from the immediate-review export (`career_engine/immediate_review.py`). See below. |
| `review_priority` | no | `high` or `medium` (Codex #73 shortlisted candidates), or `prioritize`, `consider`, `verify_first`, `deprioritize`, or `unassessed` (rubric bands in `docs/prospect-ai-assessment-v1.md`). Anything else shows as `unassessed`. |
| `availability_status` | no | `not_yet_verified`, `verified_current`, or `no_longer_open` (the values validated by `tools/prepare_shortlist.py`). Anything else shows as `not_yet_verified`. |
| `availability_checked_at` | with `verified_current` or `no_longer_open` | When the posting was actually checked: `YYYY-MM-DD` or a timezone-aware ISO timestamp (`Z` or `±HH:MM`), shown in UTC. `null` for `not_yet_verified`. `verified_current` without a valid time is shown as **NOT YET VERIFIED**. |

## Listing data and source

Each card shows everything Prospect captured for the listing, instead of
model-written reasons. Pass the preferred variant's `data` from the
immediate-review export unchanged as `listing`.

- **Pulled from the listing** table: `title`, `company`, `location`, `geography`,
  `work_arrangement`, `employment_type`, `category`, `posted_at`, `updated_at`,
  `closing_at`, `record_kind`, `detail_status`, `evidence_status`, then any other
  keys in `listing` (up to 30, keys matching `[A-Za-z0-9][A-Za-z0-9._-]*`).
- **Source** table: `source`, `provider`, `board_key`, `source_id`, `identity`,
  `retrieved_at`, `description_truncated`, `description_sha256`,
  `description_text_sha256`, `omitted_urls`, and links to `url` (the listing as
  collected) and `original_posting_url`. Links are labelled with their host and
  open the exact URL; SHA-256 digests show their first 16 characters.
- **Show full description**: the whole `description` (up to 50,000 characters)
  with its line breaks, in an expander.
- Every field is always listed. Null or blank values show as *not captured*.
  Values hidden by redaction show as *withheld*, so a hidden value is never
  mistaken for a missing one. Values are shown as captured, not reformatted.
- In the description only unsafe words (links, email addresses, credentials) are
  replaced with `[redacted]`; the rest of the text is kept.
- Without `listing`, the card names the board from the posting link's host
  (for example Greenhouse) and says the file has no extracted fields.

## Untrusted data

Every field is treated as untrusted text. Values pass Prospect's existing
metadata redaction (`career_engine.store.privacy.Projection`): text that looks
like an email address, URL, path, or credential is dropped, and Markdown is
escaped. A link is shown only when the same projection accepts it as an ordinary
public HTTPS URL. Unsafe or missing links show "Original posting link unavailable".
The exact accepted URL is kept (minus any `#fragment`), so the link opens the
original posting.

## Local feedback (`would apply / maybe / no`)

Each card has a **Your take** control. For a non-demo shortlist, choices are
saved atomically to `shortlist-feedback.json` beside the shortlist (mode 600),
or to `PROSPECT_SHORTLIST_FEEDBACK` if set. Feedback is never written to the
discovery database, and the view refuses to save it anywhere inside this
repository, so it cannot end up in Git. For a demo file, or a file inside the
repository, reactions are kept only for the browser session.

```json
{"schema_version": 1, "entries": {"<review_id>": {"decision": "would_apply", "recorded_at": "2026-11-15T08:00:00Z"}}}
```

`decision` is `would_apply`, `maybe`, or `no`. A future assessment run can read
this file as input. The dashboard does not send it anywhere.
