"""Pinned, opt-in public ATS catalogs. Catalog rows are source observations."""
from collections import Counter
import csv
from dataclasses import dataclass
from hashlib import sha256
import io
import math
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import unquote, urlsplit

from career_engine.ats import PUBLIC_HOSTS, public_board_url
from career_engine.board_discovery_http import RequestsTransport, USER_AGENT, access_stop, retry_seconds
from career_engine.board_indexes import PROBE_TEMPLATES, candidate
from career_engine.extract import safe_public_url
from career_engine.registry_state import parse_json
from career_engine.store.blobs import digest
from tools.pilot39_robots import RobotsRules

CATALOG_CAMPAIGN = "public-catalogs"
RAW_HOST = "raw.githubusercontent.com"
ROBOTS_URL = f"https://{RAW_HOST}/robots.txt"
DATA_BYTES = 32 * 1024 * 1024
POLICY_BYTES = 1024 * 1024
SUPPORTED = frozenset(PROBE_TEMPLATES)
UNSUPPORTED = {"recruitee", "rippling"}
PROVIDERS = SUPPORTED | UNSUPPORTED
CSV_FIELDS = {"ats_vendor", "company_name", "board_slug", "last_crawled"}
STATUSES = {"ok", "dead", "unconfirmed", "withdrawn"}
BOARD_URL_FIELDS = ("board_url", "url", "careers_url", "job_board_url", "site")
SOURCE_FIELDS = ("source", "sources", "source_url", "source_urls", "evidence", "evidence_url",
                 "source_evidence", *BOARD_URL_FIELDS)
URL_REF_FIELDS = {*BOARD_URL_FIELDS, "source_url", "source_urls", "evidence_url", "endpoint"}
REF_FIELDS = dict.fromkeys((*SOURCE_FIELDS, "endpoint", "kind", "id", "name", "type", "date",
                           "timestamp", "method", "status", "http_status", "checked_at"))


@dataclass(frozen=True)
class Source:
    name: str
    repo: str
    path: str
    license: str
    license_url: str
    attribution: str
    attribution_url: str
    blocklist_path: str = ""

    def url(self, revision, path):
        return f"https://{RAW_HOST}/{self.repo}/{revision}/{path}"


SOURCES = {
    "last-round-ai": Source(
        "LastRound AI ATS Company Directory", "fyrosofttech/lastroundai-hiring-data",
        "ats-directory/lastroundai-ats-company-directory-2026-08.csv",
        "CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/",
        "LastRound AI", "https://lastroundai.com/blog/salary-transparency-study-2026",
    ),
    "moonie": Source(
        "moonie0201 ATS Directory", "moonie0201/ats-directory", "companies.jsonl",
        "CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/",
        "moonie0201", "https://github.com/moonie0201/ats-directory", "blocklist.txt",
    ),
}


@dataclass(frozen=True)
class Artifact:
    url: str
    sha256: str
    file: Path | None
    limit: int


@dataclass(frozen=True)
class Catalog:
    source_id: str
    revision: str
    data: Artifact
    blocklist: Artifact | None


def _read_file(path, limit):
    if not path.is_file():
        raise ValueError("Catalog inputs must be regular files")
    with path.open("rb") as file:
        body = file.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Catalog input exceeds its byte limit")
    return body


def _artifact(config, prefix, revision, url, parent, limit):
    path = config.get(prefix + "file")
    expected = config.get(prefix + "sha256")
    if path is not None and (not isinstance(path, str) or not path):
        raise ValueError("Catalog file must be a path")
    if expected is not None:
        digest(expected)
    if not expected and (path is not None or revision == "main"):
        raise ValueError("Local files and main-branch downloads require an expected SHA-256 for each object")
    return Artifact(url, expected or "", parent / Path(path).expanduser() if path is not None else None, limit)


def read_manifest(path):
    """Parse operator pins. The manifest cannot introduce a remote URL or license."""
    manifest = parse_json(_read_file(path, 64 * 1024))
    if not isinstance(manifest, dict) or not manifest or manifest.keys() - (SOURCES.keys() | {"policy"}):
        raise ValueError("Expected a catalog manifest keyed by last-round-ai and/or moonie")
    catalogs = []
    for source_id, source in SOURCES.items():
        if source_id not in manifest:
            continue
        config = manifest[source_id]
        fields = {"revision", "sha256", "file"}
        if source.blocklist_path:
            fields |= {"blocklist_sha256", "blocklist_file"}
        if not isinstance(config, dict) or config.keys() - fields:
            raise ValueError(f"Unexpected manifest fields for {source_id}")
        revision = config.get("revision", "main")
        if not isinstance(revision, str) or not (revision == "main" or re.fullmatch(r"[0-9a-f]{40}", revision)):
            raise ValueError("Catalog revision must be a full Git commit SHA or main with SHA-256 pins")
        data = _artifact(config, "", revision, source.url(revision, source.path), path.parent, DATA_BYTES)
        blocklist = None
        if source.blocklist_path:
            blocklist = _artifact(config, "blocklist_", revision, source.url(revision, source.blocklist_path),
                                  path.parent, POLICY_BYTES)
        catalogs.append(Catalog(source_id, revision, data, blocklist))
    policy = None
    if "policy" in manifest:
        config = manifest["policy"]
        if not isinstance(config, dict) or set(config) != {"file", "sha256"} or not config["file"]:
            raise ValueError("Catalog policy requires a local file and its SHA-256")
        policy = _artifact(config, "", "main", "", path.parent, POLICY_BYTES)
    return catalogs, policy


class CatalogTransport(RequestsTransport):
    origins = (RAW_HOST,)

    def __init__(self, contact, artifacts):
        super().__init__(contact)
        self.urls = {artifact.url for artifact in artifacts} | {ROBOTS_URL}

    def request(self, method, url, byte_limit, seconds):
        if method != "GET" or url not in self.urls:
            raise ValueError("Unexpected catalog request")
        return super().request(method, url, byte_limit, seconds)


def _download(state, transport, artifacts, timeout, clock, sleep):
    def request(url, cap):
        response = transport.request("GET", url, cap, timeout)
        stopped = access_stop(response)
        if stopped:
            state.set_origin(RAW_HOST, clock(), stopped)
        if response.status in {429, 503}:
            delay = retry_seconds(response.headers.get("retry-after", ""), clock(), 60)
            state.set_origin(RAW_HOST, clock() + delay, "")
        if stopped or response.error or not 0 <= len(response.body) <= response.bytes_read <= cap:
            raise ValueError("Catalog download failed or access stopped")
        return response

    not_before, stopped = state.origin(RAW_HOST)
    if stopped or not_before > clock():
        raise ValueError("Catalog origin is stopped or cooling down")
    robots = request(ROBOTS_URL, 512_000)
    if robots.status == 200 and not robots.body.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        rules = RobotsRules(robots.body.decode("utf-8-sig"), USER_AGENT)
    elif robots.status in {404, 410}:
        rules = RobotsRules("", USER_AGENT)
    else:
        raise ValueError("Catalog robots rules could not be established")
    if rules.unsupported or any(not rules.decision(item.url)[0] for item in artifacts):
        raise ValueError("Catalog route or pacing denied by robots policy")
    state.set_origin(RAW_HOST, clock() + rules.delay, "")
    for item in artifacts:
        not_before, _ = state.origin(RAW_HOST)
        if not_before > clock():
            sleep(not_before - clock())
        state.set_origin(RAW_HOST, clock() + rules.delay, "")
        response = request(item.url, item.limit)
        if response.status != 200:
            raise ValueError(f"Catalog artifact returned HTTP {response.status}")
        yield item, response.body


def _verified_object(artifact, body):
    actual = sha256(body).hexdigest()
    if artifact.sha256 and artifact.sha256 != actual:
        raise ValueError("Catalog object SHA-256 does not match its manifest pin")
    return body, actual


def _objects(state, artifacts, *, offline, contact, timeout, clock, sleep):
    remote = [item for item in artifacts if item.file is None]
    if remote and (offline or not contact):
        raise ValueError("Remote catalog artifacts require --contact. Offline imports require all local files.")
    objects = {item: _verified_object(item, _read_file(item.file, item.limit))
               for item in artifacts if item.file is not None}
    if remote:
        with CatalogTransport(contact, remote) as transport:
            for item, body in _download(state, transport, remote, timeout, clock, sleep):
                objects[item] = _verified_object(item, body)
    return objects


def _slug(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("invalid_slug")
    token = unquote(value, errors="strict")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9 ._-]{0,99}", token) or token != token.rstrip():
        raise ValueError("invalid_slug")
    return token


def _text(value, limit):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or any(
            unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise ValueError("invalid_metadata")
    return value


def _first(row, *fields):
    return next((row[field] for field in fields if field in row and row[field] not in (None, "")), None)


def _board_url(value, token):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.hostname in PUBLIC_HOSTS and parsed.scheme == "https"
                and re.fullmatch(r"/[^/]+/?", parsed.path)):
            result = candidate(value, parsed.hostname)
            if result and result[0]["token"].lower() == token.lower():
                return result
    except ValueError:
        pass
    return None


def _source_refs(value, key="", depth=0):
    """Retain bounded provenance fields, without query strings or blob copies."""
    if depth < 3 and isinstance(value, (dict, list)):
        if isinstance(value, list):
            return [_source_refs(item, key, depth + 1) for item in value[:20]]
        return {field: _source_refs(value[field], field, depth + 1) for field in REF_FIELDS if field in value}
    if key not in URL_REF_FIELDS and type(value) is int and 0 <= value <= 999:
        return value
    try:
        value = _text(value, 2048)
        if (key in URL_REF_FIELDS or "://" in value or value.startswith(("/", "./", "../"))
                or key in SOURCE_FIELDS and ("?" in value or "#" in value)):
            if "\\" in value or any(char.isspace() for char in value):
                return ""
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc:
                absolute = "https:" + value if not parsed.scheme else value
                if "@" in parsed.netloc or not safe_public_url(absolute):
                    return ""
            elif value.startswith("//") or "://" in value:
                return ""
            return parsed._replace(query="", fragment="").geturl()
        return value
    except ValueError:
        return ""


def catalog_source_evidence(row):
    refs = {key: _source_refs(row[key], key) for key in SOURCE_FIELDS if key in row}
    region = _source_refs(row.get("region"))
    if isinstance(region, str) and region.lower() in {"us", "eu", "global"}:
        refs["region"] = region.lower()
    return refs


@dataclass(frozen=True)
class Observation:
    line: int
    board_key: str
    provider: str
    raw_provider: str
    company: str
    raw_slug: str
    record_date: str
    source_status: str
    disposition: str
    variant_url: str
    observed_url: str
    source_evidence: dict
    rejection: str = ""


def _route(row, provider, token):
    if provider not in SUPPORTED:
        return provider, ""
    observed = []
    for field in BOARD_URL_FIELDS:
        value = _first(row, field)
        if value is None:
            continue
        result = _board_url(value, token)
        if result is None:
            raise ValueError("invalid_board_url")
        observed.append(result)
    region = _text(row.get("region"), 32).lower()
    expected = {provider}
    if provider in {"lever", "lever_eu"}:
        if region not in {"", "us", "eu", "global"} or (provider == "lever_eu" and region == "us"):
            raise ValueError("invalid_region")
        expected = {"lever_eu"} if provider == "lever_eu" or region == "eu" else {"lever"}
        if provider == "lever" and region in {"", "global"}:
            expected.add("lever_eu")
    routes = {board["provider"] for board, _ in observed}
    if len(routes) > 1 or routes - expected:
        raise ValueError("conflicting_board_url")
    if observed:
        board, url = observed[0]
        return board["provider"], url
    return ("lever_eu" if provider == "lever" and region == "eu" else provider), ""


def _variant_url(provider, token):
    if provider not in SUPPORTED:
        return ""
    url = public_board_url(provider, token)
    if not candidate(url, urlsplit(url).netloc):
        raise ValueError("invalid_slug")
    return url


def _observation(source_id, line, row):
    if not isinstance(row, dict):
        raise ValueError("invalid_row")
    if source_id == "last-round-ai":
        raw_provider, raw_slug, company, date = (row[field] for field in
                                                 ("ats_vendor", "board_slug", "company_name", "last_crawled"))
        status = "not_reported"
    else:
        raw_provider = _first(row, "ats", "provider", "ats_vendor", "platform", "ats_type")
        raw_slug = _first(row, "slug", "board_slug", "board_token", "token")
        company = _first(row, "name", "company_name", "company", "employer")
        date = _first(row, "verified_at", "last_verified_at", "last_verified", "last_checked_at", "last_checked",
                      "last_crawled", "checked_at", "updated_at", "record_date", "date")
        status = _text(row.get("status"), 32).lower()
        if status not in STATUSES:
            raise ValueError("invalid_status")
    provider = _text(raw_provider, 32).lower().replace("-", "_")
    if provider not in PROVIDERS:
        raise ValueError("unknown_provider")
    token = _slug(raw_slug)
    variant_url = _variant_url(provider, token)
    rejection = observed_url = ""
    refs = {}
    if source_id != "last-round-ai":
        try:
            routed_provider, observed_url = _route(row, provider, token)
            variant_url = _variant_url(routed_provider, token)
            provider = routed_provider
        except ValueError as error:
            if status != "withdrawn":
                raise
            rejection = str(error)
        refs = catalog_source_evidence(row)
    disposition = "unsupported" if provider in UNSUPPORTED else "dead" if status == "dead" else "candidate"
    if status == "withdrawn":
        disposition = "withdrawn"
    try:
        company, date = _text(company, 512), _text(date, 100)
    except ValueError as error:
        if status != "withdrawn":
            raise
        rejection = rejection or str(error)
        company = date = ""
    return Observation(line, f"{provider}:{token.lower()}", provider, raw_provider, company,
                       raw_slug, date, status, disposition, variant_url, observed_url, refs, rejection)


def _records(body, source_id):
    if source_id == "last-round-ai":
        reader = csv.reader(io.StringIO(body.decode("utf-8-sig"), newline=""), strict=True)
        fields = next(reader, [])
        if not CSV_FIELDS <= set(fields) or len(set(fields)) != len(fields):
            raise ValueError("Unexpected LastRound AI CSV header")
        for record in reader:
            yield reader.line_num, dict(zip(fields, record)) if len(record) == len(fields) else None
    else:
        for line, value in enumerate(io.BytesIO(body), 1):
            value = value.removeprefix(b"\xef\xbb\xbf") if line == 1 else value
            if value.strip():
                try:
                    yield line, parse_json(value)
                except (ValueError, RecursionError):
                    yield line, None


def observations(body, source_id):
    rows, rejected = [], Counter()
    try:
        for line, record in _records(body, source_id):
            try:
                rows.append(_observation(source_id, line, record))
            except (UnicodeError, ValueError) as error:
                reason = str(error)
                rejected[reason if reason in {"invalid_row", "invalid_status", "invalid_slug", "unknown_provider",
                                             "invalid_region", "invalid_metadata", "invalid_board_url",
                                             "conflicting_board_url"} else "invalid_row"] += 1
    except csv.Error:
        raise ValueError("Malformed LastRound AI CSV") from None
    return rows, rejected


def _block_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        raise ValueError
    if parsed.netloc in PUBLIC_HOSTS:
        return {PUBLIC_HOSTS[parsed.netloc]}, _slug(parsed.path.strip("/"))
    if parsed.netloc.endswith(".recruitee.com") and parsed.path in {"", "/"}:
        return {"recruitee"}, _slug(parsed.netloc.removesuffix(".recruitee.com"))
    if parsed.netloc == "ats.rippling.com":
        return {"rippling"}, _slug(parsed.path.strip("/").removesuffix("/jobs"))
    raise ValueError


def _blocked_keys(body):
    """Accept provider:slug, provider/slug, exact hosted URLs, or bare slugs."""
    keys = set()
    for line, raw in enumerate(body.decode("utf-8-sig").splitlines(), 1):
        value = raw.partition("#")[0].strip()
        if not value:
            continue
        try:
            if "://" in value:
                providers, token = _block_url(value)
            else:
                parts = re.split(r"[:/,]", value, maxsplit=1)
                provider, value = parts if len(parts) == 2 else ("", value)
                provider = provider.strip().lower().replace("-", "_")
                if provider and provider not in PROVIDERS:
                    raise ValueError
                token = _slug(value.strip())
                providers = {"lever", "lever_eu"} if provider == "lever" else {provider} if provider else PROVIDERS
        except ValueError:
            raise ValueError(f"Invalid catalog blocklist entry on line {line}") from None
        keys.update(f"{name}:{token.lower()}" for name in providers)
    return keys


def import_catalogs(state, manifest, *, offline=False, contact="", timeout=30, clock=time.time, sleep=time.sleep):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Catalog timeout must be positive and finite")
    catalogs, policy = read_manifest(manifest)
    artifacts = [artifact for catalog in catalogs for artifact in (catalog.blocklist, catalog.data) if artifact]
    if policy:
        artifacts.append(policy)
    with state.exclusive():
        objects = _objects(state, artifacts, offline=offline, contact=contact, timeout=timeout, clock=clock, sleep=sleep)
        batches, blocks = [], {}
        for catalog in catalogs:
            body, data_hash = objects[catalog.data]
            policy_hash = ""
            if catalog.blocklist:
                rules, policy_hash = objects[catalog.blocklist]
                for key in _blocked_keys(rules):
                    blocks[key] = (catalog.blocklist.url, policy_hash)
            rows, rejected = observations(body, catalog.source_id)
            for row in rows:
                if row.disposition == "withdrawn":
                    blocks[row.board_key] = (catalog.data.url, data_hash)
            batches.append((catalog, data_hash, policy_hash, rows, rejected))
        if policy:
            rules, policy_hash = objects[policy]
            for key in _blocked_keys(rules):
                blocks[key] = ("local_policy", policy_hash)
        state.record_catalogs(batches, blocks)
    return state.summary(CATALOG_CAMPAIGN)
