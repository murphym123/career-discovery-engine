# Prospect M1b geography and replay

`career_engine.geography.classify_geo(location_raw, context)` returns a
geography value and a short explanation. It uses posting-level worksite
evidence. A bare ambiguous name, an unresolved regional boundary, and a remote
policy without clear DC eligibility stay uncertain. A positive local option
can qualify a multi-worksite posting. Where both Maryland and Virginia options
are explicit, the first listed nearby option supplies the single label. An
explicit District option takes precedence over other local options.

The [versioned gazetteer](../markets/dc/gazetteer.yaml) uses the existing
nearby-DC evidence and the reviewed locality mappings for Chantilly, Fort
Belvoir, Beltsville, Lanham, and Landover. It uses YAML's JSON syntax so the
replay command can run with the existing dependencies. The regression suite
reads location strings from the committed coverage diagnostics and the Issue
17 and 21 evidence. It includes a conflicting location/title example and
synthetic ambiguity, remote, and replay cases. [Coverage research](coverage/DC_EMPLOYMENT_COVERAGE_INTELLIGENCE.md).

GitHub identifies the November 6 discovery run `42193251891` as workflow run 8.
Its original `career-report-42193251891` artifact contains
`dc-expanded-audit.json` with 3,288 sightings. The 216 legacy regional rows
contain 74 distinct location strings. The audit also contains all 15 distinct
locations from the reviewed metro-locality diagnostics. None of either set
classifies as `elsewhere`. Some remain uncertain under the current footprint
and remote rules. This is a regression check against those location strings.
The archive and its records stay outside Git. [Original run 8 and artifact](https://github.com/murphym123/career-discovery-engine/actions/runs/42193251891).

To repeat the check with the original archive:

```sh
PROSPECT_RUN8_ARCHIVE=/path/to/career-report-42193251891.zip \
  python -m unittest discover -s tests -p 'test_prospect_geography.py' -v
```

The optional context mapping can contain an explicit posting worksite
`state` or `country`, offered `worksites`, a `work_arrangement`, a stated
`remote_scope`, and the posting `title`. Worksites can be strings or structured
addresses with separate location, state, and country fields. Contradictory
jurisdiction evidence returns `unknown` with a conflict explanation.
Country or employer location alone
does not establish permission to work remotely from DC. Telework eligibility
alone does not establish a remote role. A title is used only to flag the
documented explicit nonlocal-suffix conflict.

## Replay stored sightings

```sh
python -m career_engine.relabel --dimension geo --version gazetteer-dc/1
```

The command opens the existing database under `PROSPECT_DATA_DIR`. An explicit
`--data-dir` can override that directory. It classifies every sighting missing
that version, with no live requests. Replay uses the recorded display location
and, when available, posting-specific structured worksites from the sighting's
linked public USAJOBS or Greenhouse capture. Matching uses the stored native ID
or, when no native ID exists, the exact observed URL. A missing capture or
conflicting duplicate record does not borrow evidence from another posting.
[USAJOBS Search API reference](https://developer.usajobs.gov/api-reference/get-api-search).

The command appends `geo` labels with the requested `classifier_version`.
The existing `method` field carries the gazetteer explanation. A second run
with the same version adds labels only for newly available sightings. A later
version can append new judgments while retaining every older label. Version
changes require a new bundled rule version. A replay after capture purging can
use only evidence still available to that invocation. Posting, sighting, and
poll records stay intact.

## Decisions still open

- **M0.3** owns the role-scope codebook and how evaluation interprets examples,
  including complicated remote restrictions and mixed onsite/remote wording.
- **M0.4** owns the C0/P1/P2 populations and the final nearby-MD/VA boundary.
  The gazetteer does not adopt the full Washington MSA. Places such as
  Frederick, Columbia, Stafford, Quantico, and Fredericksburg remain
  `unknown` under this footprint. The code does not assign blanket eligibility
  to Maryland, Virginia, or the outer-ring counties.

```sh
python -m unittest discover -s tests
```
