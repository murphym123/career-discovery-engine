# Focused publication-discovery boundaries

This phase implements a small discovery/collection/audit boundary, not the full
long-term architecture in Issue #14. No new candidate evaluator, AI subsystem,
relevance gate or ranking model is implemented.

| Interface | Producer | Meaning |
|---|---|---|
| `market/v1` | `career_engine.markets` | City/state test context, local-label function and explicit geographic scope. No employers or candidate profile. DC's existing DMV geography is an optional fixture. |
| Observed publication edge | `publication_audit.offered_channels` | Public URL, publication kind, referring URL, evidence and pending state. RSS/Atom advertisements, robots sitemaps, public ATS links and generic career links. No request or vacancy assertion. |
| `job-source-audit/v1` | `tools.expand_ats` | All observed ATS records, provenance, retrieval errors, source inventory scopes and transport limits; no fit-filtered removal. Supplemental RSS stays separately attributable. |
| `job-collection/v1` | `tools.expand_ats` | Original five-source records first, followed by ATS and supplemental feed sightings. Duplicate flags do not delete rows. Missing descriptions are explicit. |
| `publication-coverage-ledger/v1` | `publication_audit.build_ledger` | Independent catalogue of observed endpoints and retrieval states. URL identity is deduplicated; discovery mechanisms remain attached. Source-scoped denominators, unknown totals and ecosystem gaps are separate from job ranking. |
| Registry schema 1 (extended compatibly) | `ats_registry` | Observed board URL evidence, rotating discovery cursors, bounded seen posting hashes and three recent source status snapshots. Not a prerequisite employer registry. |

The generic cold-start CLI is `tools.discover_publications`. A fresh output
directory starts with no employers, boards or specialty sources. It reuses
publication mechanisms, retrieval adapters and audit generation. Generic US
city/state tests require an explicit state for verified location labels; a
city-only URL suffix is only a weak routing hint. They do not infer a metro
radius or remote eligibility. DC-specific source coverage and dated gap
observations live in the DC workflow/fixture, not in that portable entry point.

`PublicationObserver` wraps the existing five-source session without adding
requests or modifying responses. It discovers publication edges in pages the
collector already reads. A discovered generic career URL stays pending until
access policy, job subject and inventory support are established. Unsupported
ATS links are recorded separately from verified inventories.

The public ATS selection budget has distinct routes: strong source/local URL
evidence, capped weak function exploration, at least 20% non-targeted exploration,
then broad fill. These route labels describe retrieval allocation, not job fit.
All retrieved nonlocal and unusual jobs remain in the collection.

Retrieval status is source-scoped: **searched** requires demonstrated metadata
completion in a finite supported endpoint; **partially_retrieved** includes
bounded HTML/feed windows or limits; **inaccessible** records denial/failure;
**not_yet_covered** includes unpolled candidates and unsupported/unreconciled
schemas. An empty public API inventory can be successfully searched. An empty
HTML parser does not establish zero vacancies. Description completion is a
separate denominator and never implies complete mandatory qualifications.

The original ranker is unchanged and only consumes the completed collection if
the user enables it. Future consumers can use the versioned JSON boundary;
this phase does not specify or build their models.
