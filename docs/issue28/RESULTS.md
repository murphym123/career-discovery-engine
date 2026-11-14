# Issue 28: House Employment Bulletin import

The manual importer accepts local PDFs and exported EML files with actual PDF attachments. It archives original bytes by SHA-256, preserves numbered raw text spans and page references, and keeps one annual ledger entry per printed MEM ID. It does not fetch attachments, subscribe, authenticate to a mailbox, send applications, or schedule runs. The House describes the bulletin as a [weekly email](https://www.house.gov/employment/positions-with-members-and-committees); this work establishes historical extraction, not access to current issues.

| Historical publisher PDF | Pages | Manually checked IDs | Retained | ID precision / recall | Page-span mismatches |
|---|---:|---:|---:|---:|---:|
| [April 20, 2026](https://content.govdelivery.com/attachments/USCAOHOUSE/2026/04/20/file_attachments/3623742/HVAPS%20Template_Members_2026_04_20_2026.pdf) | 31 | 27 | 27 | 100% / 100% | 0 |
| [May 11, 2026](https://content.govdelivery.com/attachments/USCAOHOUSE/2026/05/11/file_attachments/3647140/HVAPS%20Template_Members_2026_05_11_2026.pdf) | 19 | 17 | 17 | 100% / 100% | 0 |

I checked each of the 44 printed headings on rendered pages and reconciled its continuation pages to the retained text span. The [reviewed fixture manifest](../../tests/fixtures/house_bulletin/manifest.json) pins source URLs, issue dates, file hashes and all expected spans. The [discrepancy report](validation.json) records per-ID span hashes and field omissions. Four IDs recur across the two weeks, leaving **40 distinct annual IDs and 44 document sightings**. Replaying May did not add a new vacancy or document sighting. A synthetic EML envelope containing both authentic PDFs also produced 40 IDs; this was a MIME test, not evidence of a real publisher email.

**May remains PARTIAL COLLECTION.** Its text layer contains 31 suspicious word forms consistent with glyph loss. The importer retains the extracted text unchanged and links the original pages. Number gaps are recorded as review signals and do not count as missing postings. ID metrics do not measure prose-field accuracy. For example, worksite remained unknown for 18 April records and six May records; explicit non-DC locations were retained. Source forms, qualifications and application instructions may require checking the original PDF, especially links only present as annotations.

Invalid PDFs, image-only pages, unassigned or malformed MEM material, ambiguous boundaries, duplicate headings, and body/attachment discrepancies produce review diagnostics. An unnumbered email body remains unreconciled; a link-only email produces no invented job records. There is no silent OCR. Local source text and message artifacts stay outside git, with no personal mailbox data in public fixtures.

**Validation:** 300 tests passed locally with both pinned PDFs, including 20 new tests. The historical check is reproducible offline with the [manual workflow](USAGE.md). Existing five-source, ATS, geography, ranking and sealed review behavior is unchanged.
