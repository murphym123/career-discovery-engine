# 0002 — Prospect historical evidence roles and reference separation (M0.5)

**Status:** Proposed for product-owner approval; **not yet a sealed evaluation partition**.
**Date:** 2026-11-11 (America/Denver).
**Authority:** [M0–M2 specification](../specs/prospect-m0-m2-spec.md) §§1.1, 3 (M0.5), 5.3, 8; [export contract](0001-export-contract.md).
**Private source:** [Work evidence inventory](https://drive.google.com/drive/folders/1SjMq744PowPSZM3yXdKBqLVG3CfqD1pt). The inventory is not part of this Git repository.

## Decision to approve

Use the historical inventory primarily for **development leads, methods, and provenance**; use separately collected, prospectively sealed evidence for **coverage estimates**. Preserve a small, independent-from-*new-seeding* historical employer-discovery challenge where feasible, but never report it as market-wide job recall. A record cannot serve as development evidence and a scored reference in the *same* round. After a reference round closes it may be released to development and cannot be reused as fresh validation.

The manifest of withheld identities and listings belongs only in the private evaluation environment, not in this repository or in a Codex-visible Work output. Publish only its SHA-256 digest and non-revealing population/stratum counts here **after it has actually been sealed**. No manifest or digest has yet been created.

## Inventory facts and limitations

Read-only Work research completed on November 11, 2026:

| Input | Observed size | What it does **not** establish |
| --- | ---: | --- |
| Employer/hiring-entity records | 1,167 | Unique real organizations or current hiring sites |
| Publication channels/routes | 119 | Working, authorized, or currently active collectors |
| Listing observations | 2,385 | Unique current job vacancies |
| Provisional listing groups | 1,910 | Adjudicated job identities or market recall |
| Original Notion Job Sources rows | 63 | Fully verified source URLs (10 lack URLs) |
| Employer identity confidence | 753 observed names; 326 inferred; 88 historical-reference-only | Inferred names are verified entities |
| Source concentration | 1,501 Tom Manatos digest observations; 461 JQAS; 84 Hudson | A representative DC employment-market sample |
| Original-link gap | 2,037 observations with unresolved newsletter redirects | That their original URLs are unavailable on employer sites |

The inventory reflects previous subscriptions, searches, and preferences. The source data includes private Gmail provenance and possibly licensed text. **Do not upload the original CSVs, source email content, mailbox URLs, subscription links, or private identifiers to a code repository.**

## Source-to-role allocation

| Source family | Role for first DC rounds | Rules |
| --- | --- | --- |
| Notion **Job Sources** directory (63 entries), public websites, verified public employer/ATS routes | **Development: source/channel leads** | Verify current URL, employer identity, official/public source, terms, and date before automated collection. A directory entry is not proof of a vacancy |
| Historical Job Pipeline (60 populated rows), earlier public research and public-verifiable employer evidence | **Development examples**, excluding any later sealed R4 employer group | Use public/sanitized fields and provenance; preserve conflicts and old dates. No historical coverage claim |
| Historical employer inventory (1,167 raw entities) | **Candidate identities**; split only after deduplication and contamination screening | Do not import all as verified employers. Where R4 can be sealed, embargo *all aliases and cross-source records* for those employer groups from the released seed extract |
| Historical Gmail digests and private messages | **Private research context** | Retain in private access-controlled storage. Expose only lawful, sanitized, independently verified public posting metadata to development, never raw message data |
| Paid or licensed Tom Manatos / Traverse content | **Not authorized for Prospect ingestion or repository sharing** | Do not scrape, bulk copy, distribute, or claim reference rights without verifying authorization. Channel names and permissible aggregate methodological observations may inform planning |
| **First fresh House bulletin window** | **Evaluation reference** | Preserve as held-out until the reference window is sealed and scored. Do not release bulletin entries to development before the comparison; keep the existing House importer PR #31 out of the scored first-window ingest |
| Fresh JQAS, Hudson and Democracy Jobs issues | **Evaluation reference for declared windows**, if access and reuse terms permit | Allocate the entire issue/stream-window beforehand, not individual listings after results are known; record source availability and overlap; otherwise exclude |
| External employer frames and official career pages selected independently of Prospect | **R1 reference enumeration** | Evaluator selects employers and freezes assignments, gives researchers names and procedures but no Prospect findings; they enumerate actual openings with evidence |
| USAJOBS website/API pair | **Technical control** | Explains ingestion/matching discrepancies; not evidence of overall fragmented-market coverage |

**No double-counting:** If the same employer appears in multiple historical feeds, the employer and all name variants follow one role for the relevant historical discovery challenge. If a prospective bulletin overlaps an employer's current site, treat it as an overlapping *posting sighting*, not an independent extra vacancy.

## R4: bounded historical employer-discovery challenge

- **Purpose:** Check whether Prospect discovers some employer identities *without being handed those identities as seeds*. It is **not** an estimate of DC market coverage or source-local job recall.
- **Provisional frame:** 152 observed-name employer records currently mapped to initial pilot sectors (27 think-tank/research, 41 advocacy/civil-society, 84 legislative/elected government). These are upper-bound **candidate counts**, not an adjudicated population. The separate 73 think-tank historical-reference-only records need independent validation before they can enter any eligible frame.
- **Procedure:** Evaluation lead first normalizes employers, excludes entities already exposed through earlier Prospect code, tests, seeded boards, or research, resolves aliases and cross-channel contamination, and documents counts and exclusion reasons. If enough unexposed candidates remain, withhold roughly 20% **stratified by pilot sector** for one historical employer discovery challenge. This is an initial sizing suggestion, not a fixed performance threshold.
- **Seal:** Evaluation lead privately creates the exact roster, eligibility logic, seed, timestamp and evidence-role ledger; stores it only in the evaluation repository/private store; computes its digest and shares only digest and aggregate counts for a follow-up GitHub decision.
- **Release:** After sealing, produce a **development-safe** seed extract with all withheld identities, aliases and linked historical evidence excluded. Codex is not to receive the unrestricted CSVs for seeding.
- **Fallback:** If contamination leaves too few valid employers, explicitly record **zero R4 holdout** with the exclusion counts and rely on fresh R1 and R2 reference sets instead. Do not improvise a biased sample or silently declare the historical dataset independent.

## Evaluation protocol and safeguards

1. Define each round's submarket, geography, job types, source window and roles **before** checking Prospect output.
2. The evaluation coordinator selects and hashes the sample on the evaluation side, committing only the hash and aggregate counts to the main decision log before snapshot inspection.
3. Work/Cowork reference researchers see assigned organizations, official starting pages, the agreed codebook and evidence procedure—but **not** Prospect's postings, channel graph or matches until evidence is frozen.
4. Capture official URLs, observation times, source path, job-vs-internship-vs-intake distinctions, unresolved access, and evidence status. Respect robots, site terms and authentication constraints.
5. Use independent matching and adjudicate disputes. Separate **R1 live employer recall**, **R2 forward channel contribution**, **R4 historical employer discovery**, and **USAJOBS control** in reporting. Never combine denominators.
6. After scoring a reference set, its evidence may be marked `released_to_dev`; it is permanently barred from being represented as a fresh holdout.

## Follow-up needed to make this decision operational

- **Owner:** Approve this role policy and the pilot eligibility criteria.
- **Evaluation lead (Claude Code):** Build the sealed R4 roster/contamination audit and forward issue allocations *inside private evaluation storage*, after M2a's basic safeguards exist; provide only a digest and aggregate counts for the final M0.5 partition record.
- **Work:** Conduct a **public-source verification pass** over selected entries in Notion Job Sources, confirming exact URLs, career-site hops, applicable access requirements and identifiable employer routes. Do not visit the future held-out House bulletin in development mode during its reference window.
- **Codex:** Do not add historical source imports in M1a. Future M3 seed ingestion must consume only the released development-safe extract and comply with the source access/retention classes.
- **Shared:** Decide and record the first dated forward reference windows before anyone collects those issues for development.

**Approval consequence:** This record approves an allocation *method*, not an empirical recall score, not a particular withheld roster, not permission to extract licensed content, and not permission to merge PR #31.
