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
      "availability_checked_at": "2026-11-14"
    }
  ]
}
```

| Field | Required | Rules |
| --- | --- | --- |
| `schema_version` | yes | Must be `1`. Any other value is rejected. |
| `generated_at` | no | `YYYY-MM-DD` or UTC `YYYY-MM-DDTHH:MM:SSZ`. |
| `demo` | no | `true` shows a **DEMO SHORTLIST** banner and keeps feedback session-only. |
| `jobs` | yes | Array, at most 200 items. Order is preserved as the display order. |
| `review_id` | yes | Unique, `[A-Za-z0-9][A-Za-z0-9._-]*`. Same ID as the assessment output. |
| `title`, `employer`, `location`, `work_arrangement`, `compensation_text` | no | Plain text. Missing values show as "not stated". |
| `official_url` | no | Exact employer/ATS posting link. Must be public HTTPS. |
| `application_url` | no | Defaults to `official_url`. |
| `fit_reasons` | no | 2–3 short, evidence-based strings recommended. At most 5 are shown. |
| `gaps_to_verify` | no | Requirements or unknowns to check. At most 5 are shown. |
| `review_priority` | no | `prioritize`, `consider`, `verify_first`, `deprioritize`, or `unassessed` (the bands in `docs/prospect-ai-assessment-v1.md`). Anything else shows as `unassessed`. |
| `availability_status` | no | `verified_current`, `unverified`, or `closed`. Anything else is `unverified`. |
| `availability_checked_at` | with `verified_current` | Date the posting was actually checked. `verified_current` without a valid date is shown as **NOT YET VERIFIED**. |

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
