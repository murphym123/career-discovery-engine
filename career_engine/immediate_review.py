"""Offline, private review exports for saved public-board snapshots."""
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from career_engine.store.blobs import outside_git
from career_engine.store.privacy import Projection

FORMAT = "prospect-immediate-review/1"
RUBRIC = "prospect-ai-assessment/1"
DEFAULT_BATCH_SIZE = 50
LINK_FIELDS = ("url", "original_posting_url")
TEXT_FIELDS = (
    "source", "source_id", "identity", "provider", "board_key", "title", "company",
    "location", "geography", "posted_at", "updated_at", "closing_at", "employment_type",
    "work_arrangement", "category", "record_kind", "retrieved_at", "detail_status",
    "evidence_status", "description", "description_sha256",
)
FIELDS = (*TEXT_FIELDS, *LINK_FIELDS, "description_truncated")
MANIFEST_NAME = "manifest.json"


class ExportError(ValueError):
    """A safe diagnostic containing only fixed text and numeric input coordinates."""


def json_line(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def json_digest(value):
    return sha256(json_line(value))


def read_snapshot(path, ordinal):
    def unique_keys(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError
        return obj

    def invalid_number(_):
        raise ValueError

    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                          parse_constant=invalid_number)
        # Validate Unicode scalars and finite numbers even in unprojected fields.
        json_line(data)
    except OSError:
        raise ExportError(f"Input {ordinal} could not be read") from None
    except (ValueError, OverflowError, RecursionError):
        raise ExportError(f"Input {ordinal} must be valid UTF-8 JSON with unique object keys") from None
    if not isinstance(data, list):
        raise ExportError(f"Input {ordinal} must be an array of board objects")
    return sha256(raw), data


def text_fields(row, fields, where):
    clean = {}
    for field in fields:
        value = row.get(field)
        if field == "source_id" and type(value) is int:
            value = str(value)
        if value is not None and not isinstance(value, str):
            raise ExportError(f"{where}: {field} must be text or null")
        # Whitespace-only descriptions are unusable, but still captured bytes.
        clean[field] = value if value and (field == "description" or value.strip()) else None
    return clean


def public_link(value, projection):
    """Retain the original string only if the public projection keeps its locator."""
    if any(char.isspace() or char in "\\#" for char in value):
        return None
    projected = projection.url(value)
    if projected is None:
        return None
    original, safe = urlsplit(value), urlsplit(projected)
    if original.username is not None:
        return None
    if sorted(parse_qsl(original.query, keep_blank_values=True)) != parse_qsl(
        safe.query, keep_blank_values=True
    ):
        return None
    return value


def normalize_job(job, where, projection):
    if not isinstance(job, dict):
        raise ExportError(f"{where}: job must be an object")
    fields = text_fields(job, TEXT_FIELDS + LINK_FIELDS, where)
    truncated = job.get("description_truncated")
    if truncated is not None and type(truncated) is not bool:
        raise ExportError(f"{where}: description_truncated must be boolean or null")
    fields["description_truncated"] = truncated
    supplied_sha = fields["description_sha256"]
    if supplied_sha and not re.fullmatch(r"[a-fA-F0-9]{64}", supplied_sha):
        raise ExportError(f"{where}: description_sha256 must be a SHA-256 digest or null")

    omitted = []
    for field in LINK_FIELDS:
        if fields[field]:
            fields[field] = public_link(fields[field], projection)
            if fields[field] is None:
                omitted.append(field)
    description = fields["description"]
    fields["description_text_sha256"] = sha256(description.encode("utf-8")) if description else None
    fields["omitted_urls"] = omitted
    return fields


def employer(company, board_name, projection):
    """Name the employer for reviewers without overwriting the job's own company field.

    The fallback is the board-level ``name`` from the saved snapshot, labelled as such and
    never as verified. Board slugs, providers and URLs are not treated as employer names.
    """
    if company:
        return {"name": company, "source": "job_company"}
    if board_name and not projection.unsafe(board_name):
        return {"name": board_name, "source": "board_metadata_name"}
    return {"name": None, "source": "missing"}


def preferred_variant(variant):
    data = variant["data"]
    description = data["description"]
    body_quality = 0
    if description and description.strip():
        body_quality = {False: 3, None: 2, True: 1}[data["description_truncated"]]
    return (-body_quality, -len(description or ""), variant["variant_sha256"])


def group_records(snapshots):
    groups = {}
    sources = []
    statuses = Counter()
    missing = Counter(dict.fromkeys(FIELDS, 0))
    omitted_urls = Counter(dict.fromkeys(LINK_FIELDS, 0))
    counts = Counter(dict.fromkeys((
        "board_results", "board_results_with_errors", "source_errors", "observations",
        "observations_with_description", "observations_with_whitespace_only_description",
        "observations_flagged_truncated", "observations_without_native_key",
        "observations_with_board_employer_fallback", "observations_without_employer",
    ), 0))
    projection = Projection()
    for input_index, (input_sha, boards) in enumerate(snapshots):
        source = {"input_index": input_index, "sha256": input_sha, "boards": []}
        sources.append(source)
        for board_index, board in enumerate(boards):
            coordinate = f"Input index {input_index}, board index {board_index}"
            if not isinstance(board, dict) or not isinstance(board.get("jobs"), list):
                raise ExportError(f"{coordinate}: expected a board object with a jobs array")
            errors = board.get("errors")
            if errors is not None and not isinstance(errors, list):
                raise ExportError(f"{coordinate}: errors must be an array or null")
            metadata = text_fields(board, ("board", "provider", "status", "name"), coordinate)
            jobs = board["jobs"]
            safe_metadata = {key: projection.text(value) if value else None
                             for key, value in metadata.items()}
            source["boards"].append({
                "board_index": board_index, **safe_metadata, "observations": len(jobs),
                "error_count": len(errors or []),
                "error_sha256": [json_digest(error) for error in errors or []],
            })
            counts["board_results"] += 1
            counts["board_results_with_errors"] += bool(errors)
            counts["source_errors"] += len(errors or [])
            statuses[safe_metadata["status"] or "(missing)"] += 1
            for job_index, job in enumerate(jobs):
                position = [input_index, board_index, job_index]
                fields = normalize_job(job, f"{coordinate}, job index {job_index}", projection)
                fields["employer"] = employer(fields["company"], metadata["name"], projection)
                native_key = [
                    fields["provider"] or metadata["provider"],
                    fields["board_key"] or metadata["board"],
                    fields["source_id"],
                ]
                has_native_key = all(native_key)
                identity = ["native", *native_key] if has_native_key else [
                    "observation", input_sha, *position,
                ]
                review_id = "review-" + json_digest(identity)
                record = groups.setdefault(review_id, {
                    "review_id": review_id, "availability": "unverified",
                    "identity_basis": "provider_board_native_id" if has_native_key else "observation",
                    "variants": {},
                })
                variant_sha = json_digest(fields)
                variant = record["variants"].setdefault(variant_sha, {
                    "variant_sha256": variant_sha, "data": fields, "observations": [],
                })
                variant["observations"].append({
                    "position": position, "source_row_sha256": json_digest(job),
                })
                counts["observations"] += 1
                counts["observations_with_description"] += fields["description"] is not None
                counts["observations_with_whitespace_only_description"] += (
                    fields["description"] is not None and not fields["description"].strip()
                )
                counts["observations_flagged_truncated"] += fields["description_truncated"] is True
                counts["observations_without_native_key"] += not has_native_key
                counts["observations_with_board_employer_fallback"] += (
                    fields["employer"]["source"] == "board_metadata_name"
                )
                counts["observations_without_employer"] += fields["employer"]["name"] is None
                missing.update(field for field in FIELDS if fields[field] is None
                               and field not in fields["omitted_urls"])
                omitted_urls.update(fields["omitted_urls"])

    records = []
    for record in sorted(groups.values(), key=lambda row: row["review_id"]):
        variants = sorted(record["variants"].values(), key=preferred_variant)
        record["variants"] = variants
        record["preferred_variant_sha256"] = variants[0]["variant_sha256"]
        record["record_sha256"] = json_digest(record)
        records.append(record)
    counts.update({
        "review_records": len(records),
        "duplicate_observations": counts["observations"] - len(records),
        "records_with_multiple_observations": sum(
            sum(len(v["observations"]) for v in row["variants"]) > 1 for row in records
        ),
        "records_with_projected_variants": sum(len(row["variants"]) > 1 for row in records),
        "review_records_with_description": sum(
            row["variants"][0]["data"]["description"] is not None for row in records
        ),
        "review_records_with_nonblank_description": sum(
            bool((row["variants"][0]["data"]["description"] or "").strip()) for row in records
        ),
    })
    inventory = {
        "sources": sources, "counts": counts, "board_status_counts": statuses,
        "missing_fields_by_observation": missing, "omitted_urls_by_field": omitted_urls,
    }
    return records, inventory


def write_jsonl(path, lines):
    checksum = hashlib.sha256()
    with open_private(path) as out:
        for line in lines:
            out.write(line)
            checksum.update(line)
    return {"name": path.name, "rows": len(lines), "sha256": checksum.hexdigest()}


def open_private(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def is_private(path, required_type):
    info = path.lstat()
    return (required_type(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o077)


def verify_replay(target, manifest, files):
    expected = {entry["name"]: entry["sha256"] for entry in files}
    expected[MANIFEST_NAME] = sha256(manifest)
    if not is_private(target, stat.S_ISDIR) or {item.name for item in target.iterdir()} != expected.keys():
        raise ExportError("Existing bundle has unexpected contents or permissions")
    for name, digest in expected.items():
        path = target / name
        if not is_private(path, stat.S_ISREG) or sha256(path.read_bytes()) != digest:
            raise ExportError("Existing bundle failed checksum or permission verification")


def export_snapshots(paths, output_dir, max_records=DEFAULT_BATCH_SIZE):
    """Return the bundle directory and manifest after a complete, verified export."""
    if type(max_records) is not int or max_records < 1:
        raise ExportError("Max records per batch must be a positive integer")
    if not paths:
        raise ExportError("At least one snapshot is required")
    snapshots = sorted(
        (read_snapshot(path, i) for i, path in enumerate(paths, 1)), key=lambda pair: pair[0]
    )
    records, inventory = group_records(snapshots)
    lines = [json_line(record) for record in records]

    root = outside_git(output_dir)
    root.mkdir(mode=0o700, exist_ok=True)
    if not is_private(root, stat.S_ISDIR):
        raise ExportError("Output root must be a private directory owned by the operator")
    with tempfile.TemporaryDirectory(prefix=".review-stage-", dir=root) as staging:
        stage = Path(staging)
        corpus = write_jsonl(stage / "records.jsonl", lines)
        batches = [
            write_jsonl(stage / f"batch-{i:06d}.jsonl", lines[start:start + max_records])
            for i, start in enumerate(range(0, len(lines), max_records), 1)
        ]
        manifest_data = {
            "format": FORMAT, "rubric": RUBRIC, "mode": "offline_export",
            "max_records_per_batch": max_records, "corpus": corpus, "batches": batches,
            **inventory,
        }
        manifest = json_line(manifest_data)
        target = root / f"review-{sha256(manifest)}"
        with open_private(stage / MANIFEST_NAME) as out:
            out.write(manifest)
        if target.exists() or target.is_symlink():
            verify_replay(target, manifest, [corpus, *batches])
        else:
            try:
                stage.rename(target)
            except OSError as error:
                if error.errno not in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR):
                    raise
                verify_replay(target, manifest, [corpus, *batches])
    return target, manifest_data
