# Prospect M0–M2: Implementation Specification

**Status:** for final review before the first engineering tasks are authorized. Nothing has been implemented.
**Date:** 2026-11-12. **Builds on:** `audit/prospect-v1-architecture.md`, which stays unchanged. Where the two disagree, this file wins for M0–M2.
**Repository facts** were checked against `main` at `778c4ac` and are marked **(V)**.

---

## 1. Spencer's five amendments: assessment

All five hold up. Two of them (4 and 5) fix real errors in the v1 document. The others sharpen it. Remaining disagreements are small and are noted with each amendment.

### 1.1 Existing evidence: choose the partition after the inventory — **adopted**

The 50/50 split was premature: the right split depends on how many records exist, in which strata, and over which dates. The rule that replaces it:

- The partition is chosen **after** ChatGPT Work's inventory is delivered, from the inventory's counts alone.
- It is **fixed and hashed before anyone examines Prospect's output for those records.** The hash is committed in the Prospect repository, and the manifest itself stays on the evaluation side.
- Every record keeps its provenance: asset, sender or database, capture date and the inventory row id.
- After a reference set has been scored, it becomes development data (`released_to_dev`). It is never again used to report recall or to judge an experiment.

**Remaining point:** a held-out share of zero is acceptable only if the inventory turns out too small to give a useful interval. In that case, R4 is dropped and forward email streams carry the reference load. That decision should be recorded too, not taken silently.

### 1.2 Storage: no binary database or captures in Git — **adopted**

Spencer is right. A SQLite file that changes every run bloats Git history permanently, can't be merged, and gives a poor backup story. Raw captures make this worse, and they also carry privacy and licensing constraints that Git history can't honour, because content in history can't be removed cleanly. The replacement is in §4.

### 1.3 Independence from procedure, not vendors — **adopted, with one small reservation**

Agreed: the main guarantees are blinded reference collection, written procedures, fresh seeded samples and human verification. The v1 document had already said this in §5.2 and §13, but §9 over-weighted vendor and repository separation. Vendor choice is now a tie-breaker only.

**Reservation:** I would still keep the evaluation data out of the Prospect repository. That isn't for vendor reasons. It is the cheapest way to keep blinding in place, because coding agents read whatever is in the repository they work in. It costs one extra private repository and nothing else. Coordination stays light: one shared contract (§5), one decision log, and no standing review meetings between agents.

### 1.4 High-recall employer admission — **adopted; this corrects a v1 error**

The v1 promotion rule required DC-footprint evidence. That would have dropped exactly the cases Spencer names: unfamiliar employers, employers headquartered elsewhere, and remote roles open to DC residents. Corrected rules:

1. **Postings are never filtered by employer status.** Every sighting is kept and classified, whether or not its employer is known, framed, or has a DC footprint.
2. **Geography is a posting attribute with explicit uncertainty.** The values are `dc`, `dmv_md`, `dmv_va`, `remote_us_eligible`, `remote_eligibility_unknown`, `elsewhere` and `unknown`. A role counts as DC-eligible if its value is `dc`, `dmv_*` or `remote_us_eligible`. The values `remote_eligibility_unknown` and `unknown` are **retained and reported as their own group**, never folded into `elsewhere`.
3. **Employer admission is graded, not binary:**
   - `active`: in a frame, *or* has at least one in-scope posting that is DC-eligible.
   - `provisional`: an in-scope posting exists but its geography is `unknown`, or the footprint is unknown. Provisional employers are polled at a lower cadence, within a capped exploration budget, and are promoted or demoted as evidence arrives.
   - `rejected`: only on **positive evidence** of being out of scope, never because evidence is missing.
4. **Cost control** is through the polling budget, not through admission. The share of admitted employers that later show in-scope DC-eligible postings is reported, so the system can see if the graph is bloating.

**Remaining point:** none in principle. The provisional budget cap is a tuning parameter. It is set in configuration and reported, and it is not treated as a design decision.

### 1.5 Acceptance criteria: correctness vs empirical targets — **adopted; this corrects a v1 error**

Thresholds such as "≥30 misses", "≥80% auto-attributed" and "p < 0.1" were invented before any baseline existed. The approach that replaces them has three kinds of criteria:

| Kind | Example | How it gates |
|---|---|---|
| **Correctness** (deterministic) | Replay reproduces labels exactly; no posting is silently dropped; the sample is sealed before export | Pass/fail. These gate merges and milestones |
| **Validation on known truth** (the control) | Every discrepancy between the USAJOBS API and the website enumeration is explained | Pass/fail on *explanation*, not on a percentage. An unexplained matcher error fails |
| **Empirical performance** (recall, C1, C2, latency, attribution rate) | Within-frame recall for P1 | **Reported with intervals. No target until after two baseline rounds.** Targets are then set by Spencer from the observed baseline, and recorded with the date and the evidence behind them |

The statistical method replacing the arbitrary rules is in §6.

---

## 2. Scope of M0–M2

| Milestone | Goal | Owner |
|---|---|---|
| **M0** | Decisions, codebook draft, export contract v1, evidence partition | Spencer decides; Claude and ChatGPT draft |
| **M1** | Durable store, gazetteer and versioned labels, collectors writing through to the store, snapshot export | Codex (Prospect repository) |
| **M2** | Evaluation repository: contract validator, sealed sampler, reference intake, matcher, metrics, USAJOBS control | Claude Code (evaluation repository) |

Out of scope until M3 and later: frames, channel resolution, the employer resolver at scale, email importers, the miss attributor, experiments and scheduling.

---

## 3. M0: decisions and contracts

| # | Item | Output | Blocks |
|---|---|---|---|
| M0.1 | **Export contract v1** (§5) agreed | `docs/decisions/0001-export-contract.md` in the Prospect repository and a mirrored copy in the evaluation repository | M1.4 and M2.1. **This comes first** |
| M0.2 | **Storage location** (§4.3) | Decision record | The M1 live run only. Code is unaffected |
| M0.3 | **Scope codebook v0**: in-scope role definition for P1 and P2, geography values (§1.4), 30 worked examples | `markets/dc/codebook-v0.md` | M2 control coding. The P1/P2 baselines need v1 |
| M0.4 | **Submarket declarations** C0, P1 and P2 as data | `markets/dc/submarkets.yaml` | M2 sampler |
| M0.5 | **Evidence partition** (§1.1) | A manifest on the evaluation side and its hash in the Prospect repository | Seed imports (M3). Not M1 or M2 |
| M0.6 | **USAJOBS API key** requested by Spencer | Key stored as a secret | M1.3 live run and M2.5 |
| M0.7 | **House bulletin set as a held-out reference** for the first window | One line in the decision log | Merging PR #31 into ingest |

**Codebook agreement** is reported as a number (Cohen's κ with its interval) on 40 postings coded independently by Spencer and one agent. It is not a gate. A low κ means the codebook needs another revision before the P1 and P2 baselines, and does not block M1 or M2.

---

## 4. Storage design (replaces v1 §7.5)

### 4.1 Layout

All data lives in one directory set by `PROSPECT_DATA_DIR`, outside every Git repository:

```
$PROSPECT_DATA_DIR/
  prospect.sqlite               # WAL mode; the only mutable file
  blobs/<retention_class>/<sha[0:2]>/<sha256>.gz   # content-addressed raw captures
  exports/<snapshot_id>/        # immutable snapshot bundles (§5)
  backups/                      # sqlite3 .backup copies, rotated
```

- **The database** is backed up with SQLite's online backup (`.backup`) after every run, keeping the last 14 copies plus a monthly one.
- **Blobs are immutable**, so backing them up only ever means copying new files.
- Code reaches storage only through a `BlobStore` interface (`put(bytes, retention_class) → sha`, `get(sha)`, `exists(sha)`, `purge(class, older_than)`) and a `Store` class wrapping SQLite. **M1 implements a local-filesystem backend only.** An object-storage backend is added later, if M0.2 chooses one.

### 4.2 Retention classes

Each channel declares a class, and the blob store enforces it.

| Class | Examples | Raw retained | Exported in a snapshot |
|---|---|---|---|
| `public` | ATS APIs, public career pages, USAJOBS API | Yes. Default 18 months, then purged; sightings keep the hash | Metadata yes; descriptions as a hash only |
| `private_email` | Free subscription emails from Spencer's mailbox | Yes, **after removing** To/From/Cc headers, recipient-specific links and unsubscribe tokens before hashing | Metadata only. The employer, title, URL and location extracted from the email are allowed |
| `licensed` | Paid newsletters (for example Tom Manatos) | **Not ingested in v1** (reference-only, from v1 §4.3). If ingestion is approved later, raw is retained for replay only and never exported | Never |
| `restricted` | Anything whose terms forbid storage | Not stored. The poll records "skipped: restricted" | Never |

Purging deletes blobs, never sighting rows. A sighting whose blob has been purged is marked `raw_purged`, so replay knows to skip it.

### 4.3 Where it runs (decision M0.2)

| Option | For | Against |
|---|---|---|
| **A. Spencer's machine**, with `PROSPECT_DATA_DIR` on a disk that Spencer's existing backup covers | No new account; private by default | Runs only when the machine is on; agents see only the exports Spencer shares |
| **B. GitHub Actions plus a private object-storage bucket** with versioning | Scheduled runs (needed at M7); off-machine durability | Needs an account and a few cents a month; secrets to manage |

**Recommendation:** A for M1–M2, since the code is backend-neutral, and decide on B before M7 scheduling. The present design, in which a single-table `data/live.db` is carried between runs as an expiring artifact (V), is retired either way.

---

## 5. Shared export contract v1

This is the only interface between Prospect and the evaluation side. Both teams build against it, and changes require a new decision record and a version bump.

### 5.1 Bundle

A snapshot is a directory `exports/<snapshot_id>/`. `snapshot_id` is a ULID, an id that sorts by creation time. All files are UTF-8 JSONL with one object per line, sorted by primary id.

**`manifest.json`**
```json
{
  "contract_version": "prospect-export/1",
  "snapshot_id": "01J…",
  "created_at": "2026-11-20T14:03:11Z",
  "market_id": "dc",
  "prospect_code_sha": "…",
  "store_schema_version": 1,
  "classifier_versions": {"geo": "gazetteer-dc/1", "in_scope": "none"},
  "window": {"from": "2026-11-01T00:00:00Z", "to": "2026-11-20T14:00:00Z"},
  "run_ids": ["01J…"],
  "files": {"employers.jsonl": {"rows": 0, "sha256": "…"}, "…": {}}
}
```

**Files and required fields.** Any other field is optional, and consumers must ignore fields they don't know.

| File | Required fields |
|---|---|
| `runs.jsonl` | `run_id, started_at, finished_at, code_sha, config_sha, trigger, arm` |
| `channels.jsonl` | `channel_id, employer_id\|null, kind, platform\|null, locator, retention_class, evidence_role, verification_state` |
| `polls.jsonl` | `poll_id, run_id, channel_id, started_at, status (ok / partial / failed / skipped:<reason>), declared_total\|null, received, raw_sha\|null` |
| `employers.jsonl` | `employer_id, canonical_name, status (active / provisional / rejected / merged), origin, merged_into\|null, aliases:[{kind,value}]` |
| `postings.jsonl` | `posting_id, employer_id\|null, identity_key, first_seen, last_seen, closed_observed_at\|null` |
| `sightings.jsonl` | `sighting_id, posting_id, poll_id, channel_id, native_id\|null, url, canonical_url, title, employer_name_raw, location_raw, posted_at\|null, closing_at\|null, observed_at, description_sha\|null, raw_sha\|null, raw_locator\|null` |
| `labels.jsonl` | `subject, subject_id, dimension, value, method, classifier_version, created_at`. All versions are included, not only the current one |

### 5.2 Semantics both sides rely on

- **Ids are stable across snapshots.** A posting, employer or channel keeps its id for life. Merges are expressed through `merged_into`, never by reissuing ids.
- **`identity_key`** is `<platform>:<tenant>:<native_id>` where a native id exists, otherwise `url:<canonical_url>`. The existing `ats.posting_key` and `ats.canonical_posting_url` (V) are the starting point.
- **`canonical_url`** is Prospect's own normalization. The matcher **must apply its own normalization independently** to both sides and must not trust this field. That keeps a URL bug in Prospect from hiding misses.
- **Nothing is dropped.** Every sighting from every poll in the window appears in the export, including postings classified `elsewhere` or out of scope. Filtering is the consumer's job.
- **Time** is in UTC ISO-8601 with a `Z` suffix.
- **Private and licensed content:** description text is never exported, only `description_sha`. The rules for `private_email` and `licensed` follow §4.2.
- **Contract test:** both repositories hold the same `fixtures/export-v1-example/` bundle, with about 20 postings covering a merge, a closed posting, a failed poll, a purged blob and an `unknown` geography. Prospect's exporter must reproduce it from a fixture store. The evaluation validator must accept it and must reject 6 deliberately broken variants.

### 5.3 Reference intake format (evaluation side only, for Work, Cowork and Spencer)

This is a CSV with these columns: `ref_set_id, item_no, employer_observed, employer_homepage, title, url, location_raw, found_at, click_path, evidence_file, producer, codebook_version, in_scope, geo, notes`. An item with no `url` and no `evidence_file` is rejected at intake.

---

## 6. Statistical approach (replaces fixed thresholds)

1. **Recall-type metrics** are reported as proportions with **intervals that account for clustering.** Postings cluster within employers, so the interval comes from an employer-level bootstrap (resampling employers, 2,000 draws). The Wilson interval is shown beside it for reference. The interval width at a given sample size is itself reported, so Spencer can see what a round can and can't resolve.
2. **There are no performance targets before the baseline.** After two baseline rounds per submarket, targets may be set as "the lower bound of the 80% interval exceeds X". They are recorded in a decision record with the evidence behind them.
3. **Experiments are paired.** Arm A and arm B are compared on the same reference items, and the analysis uses only the discordant items (gains *g*, losses *l*):
   - Report *g*, *l*, and an exact (Clopper–Pearson) 90% interval for *g/(g+l)*.
   - **Adopt** if the interval's lower bound is above 0.5 and no correctness test regresses.
   - **Reject** if the upper bound is below 0.5.
   - Otherwise the result is **inconclusive**: the frozen change keeps accumulating discordant items in later rounds, pooled, up to a pre-registered maximum number of rounds (default 3). After that it is recorded as "no detectable effect".
   - This adapts to sample size automatically: few discordant items give a wide interval, which yields "inconclusive" rather than a false decision.
4. **Correctness fixes need no statistics.** A gazetteer bug that sends Arlington, VA to `elsewhere` is fixed because it is wrong, and the fix is verified by tests. Statistics apply only to changes whose benefit is an empirical claim.
5. **Counts are reported, not required.** Miss counts and automatic-attribution rates are reported with their denominators and never used as gates.

---

## 7. M1: Prospect store (Codex)

### 7.1 First assignment for Codex: **M1a, store core plus export**

**Goal:** a durable store and a contract-conforming export, with two collectors writing through to it. No change to collection behaviour.

**Build:**
1. `career_engine/store/schema.sql`, schema version 1. It contains the v1 §7.1–7.3 tables (`employer, employer_alias, frame, frame_membership, channel, run, poll, raw_capture, posting, sighting, label`), with the §1.4 corrections: employer status values `active / provisional / rejected / merged`, and channel fields `retention_class` and `evidence_role`. Use plain SQL migrations with a `schema_version` table, and no ORM.
2. `career_engine/store/__init__.py`: `Store(path)` with `begin_run`, `record_poll`, `record_sighting` (resolves or creates the posting by `identity_key` and updates `last_seen`), `add_label` (append-only), `end_run`. Collectors call these methods and never write SQL themselves.
3. `career_engine/store/blobs.py`: the `BlobStore` interface with a local-filesystem backend. It enforces the retention classes in §4.2: `restricted` is refused; `private_email` header stripping is a stub that raises until M4.
4. A **collector adapter layer**: a thin wrapper that runs an existing collector, captures each HTTP response body into the blob store, and writes polls and sightings. Do this for **USAJOBS** (`sources.retrieve`/`normalize`, V) and **Greenhouse** (`ats.retrieve_board`, V) only. The collectors' own logic should not be refactored.
5. `python -m career_engine.export --data-dir … --out …`, which produces a bundle per §5.
6. `fixtures/export-v1-example/` and a test that regenerates it byte for byte from a fixture store.

**Correctness tests (all must pass):**
- The existing `unittest` suite still passes unchanged (V: CI runs `python -m unittest discover -s tests`).
- Two runs over the same fixture responses: posting ids are stable, `first_seen` is unchanged, and `last_seen` advances.
- Every sighting has a `poll_id`; every poll with status `ok` or `partial` has `raw_sha` whenever a body was received.
- No silent drops: the sightings written equal the records the collector yielded, including records geocoded `elsewhere`.
- Labels are append-only: re-labeling adds rows, and an `UPDATE` or `DELETE` on `label` raises (enforced by a trigger).
- The export validates against the contract fixture and is deterministic: the same store gives the same file hashes.
- A purged blob makes the sighting `raw_purged` and the export still succeeds.

**Not in M1a:** the gazetteer, other collectors, live runs, the workflow and any CLI changes beyond `export`.

### 7.2 M1b: gazetteer and replayable labels

- `markets/dc/gazetteer.yaml`, holding DC; MD and VA jurisdictions inside the declared ring; ambiguous names (Arlington, Washington, Alexandria, Columbia) with disambiguation rules; and remote phrasing patterns.
- `classify_geo(location_raw, context)` returns a value from §1.4 together with a reason. It replaces `discovery.geography()` and `Market.label()` for store labels. The old functions stay in place for the existing brief until that brief is retired.
- `python -m career_engine.relabel --dimension geo --version …` relabels every sighting, from `location_raw` and from the raw blob if needed, and writes new label rows.
- **Tests:** a location test set built from run-8 strings and committed evidence (this is X1). It must have zero DMV strings labeled `elsewhere`, and every ambiguous string must come out as `unknown` or with a resolving reason. Relabeling with the same version is idempotent. Relabeling with a new version leaves the old rows untouched.

### 7.3 M1c: remaining collectors and first live run

- Collectors ported to the adapter: Lever, Ashby, JazzHR, iCIMS, the Senate collector and the DC specialty sources. They go in **one PR each**, and each PR keeps the collector's existing tests green.
- One live run on `main` in the chosen location (M0.2), followed by an export.
- **Tests:** per-collector fixture tests through the adapter. For the live run the evidence is the run's manifest, the poll status counts and the export hash, committed as a small JSON file. The data itself is not committed.

---

## 8. M2: Evaluation repository (Claude Code)

### 8.1 First assignment for Claude Code: **M2a, contract validator, sampler and intake**

Coordinated with Codex only through the §5 contract and its shared fixture. The work can start the day M0.1 is agreed, without waiting for M1a.

**Build, in a new private evaluation repository:**
1. `contract/validate.py`: validates a snapshot bundle (manifest hashes, required fields, referential integrity, id stability against a previous snapshot when one is provided). The shared fixture plus 6 broken variants act as its tests.
2. `sampling/`: `draw(frame_or_population, n, seed, strata)` gives a deterministic stratified sample. `seal(sample) → sha256` writes the sample to `sealed/` and prints the hash to be committed in the Prospect repository's decision log. `unseal` refuses to run until a snapshot whose `created_at` is later than the seal time exists.
3. `intake/`: parses the reference CSV in §5.3, rejects items without evidence, records the producer and codebook version, and flags duplicate items within a set.
4. `procedures/`: written instructions for reference producers, which are the main independence guarantee (§1.3). They cover what the producer is given (the sealed sample and the codebook only), what it must never be given, how to search an employer's site starting from its homepage, which evidence to capture, which sites are off-limits, and what Spencer verifies (a random 20%, every enumerator disagreement, and every item later scored as a miss).

**Correctness tests:** the validator accepts the fixture and rejects every broken variant; the same seed gives the same sample; the hash is stable; unsealing before a qualifying snapshot fails; intake rejects rows with no evidence.

### 8.2 M2b: matcher and metrics

- `match/`: matches reference items to sightings in the snapshot in tiers: native id, then the matcher's **own** normalized URL, then a fuzzy employer+title+location match with a score. Tier and score are recorded. Fuzzy matches above a threshold are auto-accepted, and those in a review band go to Spencer.
- `metrics/`: recall with employer-clustered bootstrap and Wilson intervals; breakdowns by submarket, geography value and channel kind; the paired discordant analysis from §6.3, ready for later use.
- **Tests:** a hand-built set of 50 reference–sighting pairs including traps (the same title at two employers; a reposted requisition with a new id; tracking parameters; a closed and reopened posting). The test requires zero wrong auto-accepts, and every trap must be resolved or routed to review.

### 8.3 M2c: USAJOBS control

- **Procedure:** on one day, with fixed filters (DC duty stations plus the codebook's in-scope series), the reference producer enumerates the USAJOBS website by hand, and Prospect runs its USAJOBS collector through M1a.
- **Pass condition (validation on known truth, §1.5):** every item in the symmetric difference between API and website results is classified as one of: timing, a filter mismatch, a matcher error, an enumerator miss, or an API/website difference. **There are no unexplained items, and no matcher errors remain uncorrected.**
- **Reported, not gated:** enumerator recall with its interval, matcher tier distribution and time spent. These calibrate later R1 rounds.

---

## 9. What can start before the Work inventory is complete

| Can start now (after M0.1) | Waits for the inventory |
|---|---|
| M1a store, blob store, export, USAJOBS and Greenhouse adapters | Evidence partition (M0.5) and its hash |
| M1b gazetteer, using run-8 and committed evidence strings | Seed imports of Notion channels and employers (M3) |
| M1c collector ports | Email-format parser fixtures and `private_email` stripping rules (M4) |
| M2a validator, sampler, intake, procedures | R4 held-out employer set |
| M2b matcher and metrics | Assigning forward email streams to ingest or reference (except the House bulletin, M0.7) |
| M2c USAJOBS control (once the API key arrives) | Codebook v1 refinements drawn from Spencer's historical categories (v0 can be drafted now) |

The critical path is **M0.1 (contract) → M1a ∥ M2a → M2b → M1c live run + M2c control**. The inventory isn't on it until M3.

---

## 10. Coordination, kept proportionate

- **One decision log** (`docs/decisions/` in the Prospect repository). Each decision is a short file: the question, the decision, the evidence, and who approved it (Spencer).
- **One interface:** the export contract. Codex and Claude Code don't review each other's whole codebase. Each reviews only the other's contract fixture and contract tests.
- **Code review:** Claude Code reviews Codex's M1 PRs for correctness against this spec. Codex, or Spencer, reviews the M2 contract tests. Strategy review (Claude and ChatGPT) happens once at each milestone gate, not per PR.
- **Spencer's time in M0–M2** [E]: about 3–4 hours for decisions and the codebook; about 1 hour to code 40 postings for κ; about 1–2 hours to verify the control day.

---

## 11. Remaining disagreements, briefly

1. **Repository separation (1.3).** I would keep the evaluation data in its own private repository as the cheapest form of blinding. If Spencer prefers one repository, the minimum alternative is to keep sealed samples and reference items out of any repository that the discovery agent works in. In practice that is the same thing.
2. **Zero hold-out (1.1).** Acceptable only as a recorded decision based on the inventory's size, not as a default.
3. **None on 1.2, 1.4 or 1.5.** In those three cases the amendment is simply better than what I had.
