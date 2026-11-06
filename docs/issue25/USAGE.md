# Employer-frame pilot usage

Acquire one permitted public frame and freeze a development batch before inspecting employers:

```sh
python -m tools.freeze_employer_frame --output-dir work/dc-frame --size 30
python -m tools.discover_employers \
  --frame work/dc-frame/batch.json \
  --registry data/ats-registry.json \
  --output-dir output/employer-discovery \
  --base output/dc-source-audit.json
```

`--base` is optional. It is never modified; `combined-audit.json/.csv` retains all original records plus all collected employer-frame records. Raw unranked JSON/CSV, employer and channel exports, a source ledger and review queue are separate outputs. Registry boards and verified employer-site/channel evidence use the existing state file, committed atomically only after reports finish. For experiments, use a **copy** of the registry. No action workflow or schedule was added; these are explicit manual commands.

The frozen batch JSON may also be supplied as a non-secret CSV. Required row fields are `frame_id`, `legal_name`, `source_url`, `source_observed_at` (ISO timestamp), `source_sha256` (64 lowercase hex characters), `eligibility` (`eligible`, `ineligible`, `unknown`) and `footprint`. Optional fields are `website_candidate`, `eligibility_basis`, `operating_name_claim`, `parent_affiliate_claim`. Unknown columns, including contact fields, are not imported. The input file is bounded at 4 MB; a run accepts at most 40 rows. `select_batch` can select from a larger normalized frame with a fixed hash seed. Input source URLs and website claims are evidence to verify, not authorization to bypass access checks or proof of an operating/legal employer.

Limits: 120 discovery plus 60 collection requests by default, at most 180 combined, three documents and 200 retained channel leads per employer, 600 KB/page prefixes, 15 supported board groups, two optional details per board, 32 MB and 300 seconds per stage. A limited prefix contributes only complete observed links and is explicitly queued for review. Supported collectors keep their existing response, pagination and access limits. Every retrieved posting remains unranked. `configured` API sources, other markets and experimental ranking are not invoked by this command.

`review-queue.json` includes a separate optional research contract. A researcher may work only on unresolved cases and return `frame_id`, `proposed_url`, `evidence_url`, `relationship_note`. No proposal is automatically collected or inserted. Verify the named operating entity and the public source that actually links to the proposed site, then supply the traceable evidence in a new reviewed frame. Guessed tenant names, login/challenge bypass, private APIs and applications are excluded. Functional application inboxes may be retained; personal local-parts are redacted. A hiring-channel lead or retrieved HTML page is not a successful empty job inventory.

Offline comparison from the retained task evidence archive:

```sh
python -m tools.compare_employer_discovery \
  --employer PATH/employer-final-reviewed/employer-source-audit.json \
  --global-audit PATH/global-board-first/ats-source-audit.json \
  --registry PATH/prior-registry.json \
  --output /tmp/employer-comparison.json
python -m unittest discover -s tests -v
```

The existing global mode is still `python -m tools.expand_ats --strategy legacy ...`. The empirical comparison uses separate copies of one frozen registry and different live windows; it is observational. It does not equate frame entities, board groups, job identities or qualified vacancies.

Official frame contract: [DC CBE source](https://hub.arcgis.com/datasets/dc299bc701f84f12b927eee9b3ec5a57_33/about), [ArcGIS query pagination and fields](https://developers.arcgis.com/rest/services-reference/enterprise/query-map-service-layer/).
