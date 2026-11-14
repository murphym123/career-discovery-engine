# Manual House Employment Bulletin intake

Install Poppler (`brew install poppler` on macOS, or `poppler-utils` on Debian/Ubuntu). The importer uses `pdfinfo` and `pdftotext`, with no new Python dependency.

```sh
python -m tools.import_house_bulletin path/to/bulletin.pdf \
  --output-dir output/house-bulletin
python -m tools.import_house_bulletin path/to/exported.eml \
  --output-dir output/house-bulletin
```

You can pass multiple local files. For one PDF, `--source-url` records a caller-supplied public HTTPS PDF attribution without fetching or verifying the URL. In EML, only actual attached PDF bytes are parsed. Official GovDelivery PDF URLs seen in the body are retained as evidence and associated with attachments only on exact decoded filename matches. No URL, nested message or executable attachment is opened. Raw EML is retained locally, so use a private output directory and review files before sharing. Public tests use synthetic headers and never use a mailbox export.

Each invocation writes a separate `imports/<id>/` folder with `house-bulletin.json/.csv/.md` and a compatible `coverage-audit.json/.csv/.md`. The original PDFs and EMLs are linked by hash under `evidence/`. The JSON contains the page and line of each MEM occurrence, zero-based text offsets, retained raw sections, issue date, published worksite and office evidence, and source application passages. PDF dates, email dates and import observation times remain distinct. Unknown prose fields remain unknown. A source URL with a page fragment is a document citation, not a current application URL.

`vacancy-ledger.json` is updated atomically after the audit outputs are written, with first/last observation times, immutable source references, issue dates, and document-level sightings. Each annual MEM suffix remains part of identity. Distinct issue dates mark republication; separate versions in the same week are retained without implying a new weekly appearance. Extracted-text version counts can change because of layout or glyph differences. An omission from a later issue does not prove closure. To combine with another channel, use the unranked records and coverage audit; existing collection and ranking workflows are unchanged.

Exit codes: `0` means observed PDF text reconciled; `2` means **PARTIAL COLLECTION** with review diagnostics and surviving data written; `1` means import failed. Read the per-document and email diagnostics rather than interpreting zero retained rows as zero vacancies. Bounds are 16 local files and 64 MB per invocation, 16 MB/PDF, 32 MB/EML, 100 pages/PDF, 32 distinct PDFs, 4 MB extracted text/PDF and 12 MB across a batch, 64 MIME parts, and 16 PDF parts per message. Poppler calls have a 30-second timeout each. Caps or unreadable pages are reported; OCR is out of scope.

For historical validation, obtain the exact two public PDFs listed in the [fixture manifest](../../tests/fixtures/house_bulletin/manifest.json) and save them under the named filenames. The validator checks both hashes before comparing every ID and page span. CI runs local synthetic integration tests; the pinned historical PDFs are intentionally supplied separately.

```sh
python -m tools.validate_house_bulletin /path/to/fixtures --output /tmp/house-discrepancies.json
HOUSE_BULLETIN_FIXTURES=/path/to/fixtures python -m unittest discover -s tests -v
```

For current weekly evidence, the owner can [subscribe through the House](https://www.house.gov/employment/positions-with-members-and-committees) and supply a downloaded PDF or EML. The importer has no subscription or mailbox access step.
