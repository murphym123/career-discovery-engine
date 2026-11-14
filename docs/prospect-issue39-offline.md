# Issue #39 offline pilot preparation

The [owner approval](https://github.com/murphym123/career-discovery-engine/issues/39#issuecomment-6870492680) covers offline preparation. `tools/pilot39.py` has no socket, HTTP client, live collection command, or repeat command. Its synthetic transport exercises the reviewed guard at the request boundary. The terms and repeat ceilings are reserved design limits. There is no route that spends them. No changes to collectors, storage schema, export contract, evaluator, or workflows.

## Commands

Use the normal project virtual environment from the repository root. Put the 24 canonical board URLs from [the approved cohort](https://github.com/murphym123/career-discovery-engine/issues/39#issuecomment-6870435519) in a private text file, one URL per line in poll order. The precheck enforces the ordered URL hash. The run-specific configuration, sources and all outputs belong outside Git.

```sh
python -m tools.pilot39 precheck --cohort /absolute/private/cohort.txt
python -m unittest discover -s tests -p test_pilot39.py -v
PROSPECT_EVAL_CHECKOUT=/absolute/private/prospect-evaluation \
  python -m unittest discover -s tests -v
```

The fixture integration test runs both arms, writes the plan, polls, safe request receipts, public synthetic captures and SQLite backups to a temporary directory, replays geography and exports the actual eight-file `prospect-export/1.1` bundle. It reproduces all eight files with the original manifest inputs. With `PROSPECT_EVAL_CHECKOUT` it also validates the generated snapshot using `contract.validate`. Use evaluator revision `ec8b96825e2b4770e90c0f360af1582d2d2c72c5`, verified for the approved version. A simulated interruption preserves committed evidence and an unfinished run, which the exporter rejects. Fixture requests are an exact ordered sequence and the test case blocks socket connections.

## Limits to review

- Routes start denied. Every source route requires explicit terms permission and robots clearance on the requested host and path. Unsupported robots directives or ambiguous rule precedence, unadvertised sitemap paths, changed tenant or path, and undocumented endpoints can force skips. A missing robots file permits an exact route only when that route has explicit terms clearance. Only a same-route, same-host redirect can be followed, subject to the same checks. Request, deadline, byte and response-content controls are exercised with a synthetic clock and chunks.
- The test driver counts each attempted GET, including robots, redirects and failures. Stage ceilings are 16 / 10 / 96 / 26 / 12 against 160 total with no rollover. A per-board or stage cap can leave inventory incomplete. Denial and challenge suppress later calls to the same host, including across arms. Private response signals block capture and further collection. The conservative HTML/JSON checks can reject benign templates that carry private-looking state. No live provider behavior is established by these fixtures.
- Run-specific evidence stays outside Git. The fixture plan freezes cold candidates before polling. The fixture ledger records local receipts and status. A live operational report, real scope judgments and manual listing review need actual authorized evidence. For a live run, freeze the runner and config hashes and retain the exact command, window, receipts and artifact hashes in the private run root.
- Before considering execution, require independent review and passing CI, endpoint-specific terms/robots and retention decisions, a reviewed real anonymous HTTP transport with bounded streaming and retries disabled, confirmation of the Mac permissions/encryption and backup restore, and evaluation-custodian clearance for the development exposure ledger. Validate the real transport controls independently before any use. This PR exposes only the offline commands above. The optional repeat and the live pilot require separate explicit owner approval.
