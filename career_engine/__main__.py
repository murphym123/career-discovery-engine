"""Bounded, discovery-first job search with sqlite history."""
import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import requests
from career_engine.extract import enrich_public_page

from career_engine.queries import query_plan
from career_engine.sources import SourceStats, free_sources, retrieve, REQUIREMENTS
from career_engine.discovery import geography, expired, deduplicate, exploration_sample, screening_hold
from career_engine.dc_sources import DC_SOURCES, retrieve_dc

QUERIES = [query.text for query in query_plan(20)]
LANES = {
    "policy": ("policy", "legislative", "government affairs", "public affairs", "regulatory", "advocacy"),
    "research": ("research", "analysis", "analyst", "intelligence", "insights"),
    "operations": ("program", "operations", "coordinator", "special assistant", "executive assistant", "events", "outreach"),
    "communications": ("writing", "communications", "editorial", "content", "media", "press"),
    "campaigns": ("campaign", "political", "voter", "election"),
    "development": ("development", "fundraising", "donor", "membership", "grants"),
    "consulting": ("consulting", "advisory", "strategy", "strategic", "client"),
    "executive_support": ("executive assistant", "special assistant", "staff assistant", "office of the president")
}
SENIOR = ("senior director", "vice president", "chief financial officer", "chief technology officer", "principal engineer")
TRACK = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid", "ref", "trk"}

def canonical(url):
    try:
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return ""
        qs = urlencode(sorted((k,v) for k,v in parse_qsl(u.query) if k.lower() not in TRACK and not k.lower().startswith("utm_")))
        return urlunsplit(("https", u.hostname.lower(), u.path.rstrip("/") or "/", qs, ""))
    except ValueError:
        return ""

def norm(s):
    return re.sub("[^a-z0-9]+", " ", str(s or "").lower()).strip()

def clean(raw):
    j = {k: str(raw.get(k, "") or "") for k in ("title", "company", "location", "url", "snippet", "description", "source", "posted_at", "source_id", "closing_at", "sources", "index_url", "discovery_urls", "raw_location", "retrieved_at", "evidence_status", "detail_status", "record_kind")}
    j["url"] = canonical(j["url"])
    if not j["url"] or not j["title"].strip():
        return None
    identity = "|".join(norm(j[k]) for k in ("company", "title", "location")) if j["company"] and j["title"] else j["url"]
    if j["source_id"]:
        identity = "|".join((j["source"], j["source_id"]))
    j["sources"] = j["sources"] or j["source"]
    j["key"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return j

def score(j):
    title = norm(j["title"])
    body = norm(" ".join((j["title"], j["snippet"], j["description"][:1500])))
    lane_scores = {name: sum(12 if phrase in title else 3 if phrase in body else 0 for phrase in phrases) for name, phrases in LANES.items()}
    lane = max(lane_scores, key=lane_scores.get)
    value = lane_scores[lane] + min(18, 3 * sum(x in body for x in ("policy", "research", "political", "public affairs", "communications", "program", "government")))
    location = norm(j["location"])
    if geography(location) == "dc_dmv": value += 18
    elif geography(location) == "us_remote": value += 7
    elif location and "worldwide" not in location: value -= 12
    if any(x in title for x in ("associate", "assistant", "coordinator", "junior", "entry level", "analyst", "correspondent")): value += 10
    if any(x in title for x in SENIOR): value -= 50
    if any(x in body for x in ("phd required", "doctoral degree required", "10 years of experience")): value -= 24
    return max(0, min(value, 100)), lane

def select(jobs, top):
    if top <= 0:
        return []
    selected, lane_counts, title_counts, company_counts = [], {}, {}, {}
    for j in sorted(jobs, key=lambda x:x["score"], reverse=True):
        lane, title, company = j["lane"], norm(j["title"]), norm(j["company"])
        if lane_counts.get(lane, 0) >= max(3, top // 2) or (title and title_counts.get(title, 0) >= 2) or (company and company_counts.get(company, 0) >= 2):
            continue
        selected.append(j)
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        title_counts[title] = title_counts.get(title, 0) + 1
        company_counts[company] = company_counts.get(company, 0) + 1
        if len(selected) >= top: break
    return selected

def brave(session, query, count):
    key = os.getenv("BRAVE_API_KEY")
    if not key: raise RuntimeError("BRAVE_API_KEY secret not configured")
    res = session.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": min(count, 20)}, headers={"X-Subscription-Token": key, "Accept":"application/json"}, timeout=25)
    res.raise_for_status()
    for item in res.json().get("web", {}).get("results", []):
        yield dict(title=item.get("title",""),company="",location="",url=item.get("url",""),snippet=item.get("description",""),source="brave")

def remotive(session):
    res = session.get("https://remotive.com/api/remote-jobs", timeout=25)
    res.raise_for_status()
    for j in res.json().get("jobs", []):
        yield dict(title=j.get("title",""),company=j.get("company_name",""),location="Remote; " + (j.get("candidate_required_location") or "eligibility unknown"),url=j.get("url",""),snippet=j.get("category",""),description=re.sub("<[^>]*>", " ", j.get("description",""))[:10000], source="remotive",posted_at=j.get("publication_date",""))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sources", default="dc", help="dc (Senate + Union Jobs regional discovery), free, remotive,jobicy,muse,reliefweb,usajobs,brave,csv")
    p.add_argument("--dc-detail-budget", type=int, default=20, help="Max public detail pages per DC source (0–30)")
    p.add_argument("--source-pages", type=int, default=6, help="Max requests per added API (1–20)")
    p.add_argument("--query-offset", type=int, help="Override saved query rotation (default: continue from history)")
    p.add_argument("--allow-paid-search", action="store_true", help="Explicitly authorize Brave requests, which may be billed")
    p.add_argument("--exploration-slots", type=int, default=5, help="Separate review slots for below-threshold/uncertain candidates")
    p.add_argument("--import-csv")
    p.add_argument("--query-budget", type=int, default=6)
    p.add_argument("--results-per-query", type=int, default=20)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-score", type=int, default=22)
    p.add_argument("--max-extract", type=int, default=0, help="Max distinct public HTML pages to inspect (0 disables)")
    p.add_argument("--db", default="data/live.db")
    p.add_argument("--output", default="output/live_brief.md")
    args = p.parse_args()
    if not 1<=args.query_budget<=20 or not 1<=args.results_per_query<=20 or not 0<=args.max_extract<=30 or not 1<=args.source_pages<=20 or (args.query_offset is not None and args.query_offset<0) or not 0<=args.exploration_slots<=50 or args.top<1 or not 0<=args.dc_detail_budget<=30:
        p.error("invalid search or extraction budget")
    requested = [name.strip() for name in args.sources.split(",")]
    sources = list(dict.fromkeys(name for source in requested for name in (DC_SOURCES if source == "dc" else free_sources() if source == "free" else [source])))
    if "brave" in sources and not args.allow_paid_search:
        p.error("Brave may charge; use --allow-paid-search only after authorizing charges")
    raw, errors, source_stats = [], [], {}
    skipped = [name for name in REQUIREMENTS if name not in sources] if "free" in requested else []
    query_offset = args.query_offset or 0
    if "brave" in sources and args.query_offset is None and Path(args.db).exists():
        with closing(sqlite3.connect(args.db)) as history:
            if history.execute("SELECT 1 FROM sqlite_master WHERE name='engine_state'").fetchone():
                saved = history.execute("SELECT value FROM engine_state WHERE name='query_offset'").fetchone()
                query_offset = int(saved[0]) if saved else 0
    queries = query_plan(args.query_budget, query_offset) if "brave" in sources else []
    s = requests.Session()
    for source in sources:
        stats = source_stats[source] = SourceStats()
        start = len(raw)
        try:
            if source in DC_SOURCES:
                raw.extend(retrieve_dc(s,source,args.dc_detail_budget,stats))
                errors.extend(source+": "+message for message in stats.errors)
            elif source in ("jobicy", *REQUIREMENTS):
                raw.extend(retrieve(s, source, args.source_pages, stats))
                errors.extend(source + ": " + message for message in stats.errors)
            elif source == "remotive":
                stats.requests += 1
                raw.extend(remotive(s))
            elif source == "brave":
                import time
                for q in queries:
                    stats.requests += 1
                    try: raw.extend(brave(s,q.text,args.results_per_query))
                    except requests.RequestException as e:
                        stats.status = "partial_failure"
                        errors.append("Brave query: "+type(e).__name__)
                    time.sleep(0.25)
            elif source == "csv" and args.import_csv:
                with open(args.import_csv,encoding="utf-8-sig",newline="") as f: raw.extend(csv.DictReader(f))
            else: errors.append("Unsupported source: "+source)
        except (requests.RequestException, RuntimeError, OSError, ValueError) as e:
            stats.status = "failed"
            errors.append(source+": "+type(e).__name__)
        if source not in (*DC_SOURCES, "jobicy", *REQUIREMENTS):
            stats.received = len(raw) - start
    prepared = []
    invalid_records = 0
    for item in raw:
        j = clean(item)
        if not j:
            invalid_records += 1
            continue
        j["score"],j["lane"] = score(j)
        j["extraction_status"] = "source_description" if j["description"] else "not_attempted"
        prepared.append(j)
    unique = {j["key"]: j for j in deduplicate(prepared)}

    extraction_counts = {}
    if args.max_extract:
        # Exploit relevance, but reserve one quarter for atypical search leads.
        pool = [j for j in unique.values() if j["source"] == "brave" and not j["description"]]
        ranked = sorted(pool, key=lambda j: j["score"], reverse=True)
        priority = max(1, args.max_extract - args.max_extract // 4)
        chosen = ranked[:priority]
        chosen_urls = {j["url"] for j in chosen}
        remainder = [j for j in ranked if j["url"] not in chosen_urls]
        chosen += sorted(remainder, key=lambda j: hashlib.sha256(j["url"].encode()).hexdigest())[:args.max_extract - len(chosen)]
        robots_cache = {}
        for j in chosen:
            fields, status = enrich_public_page(s, j["url"], j["title"], robots_cache)
            j["extraction_status"] = status
            extraction_counts[status] = extraction_counts.get(status, 0) + 1
            if status == "jobposting_metadata":
                for key in ("title", "company", "location", "description", "posted_at"):
                    if fields.get(key):
                        j[key] = fields[key]
                updated = clean(j)
                if updated:
                    j["key"] = updated["key"]
                j["score"], j["lane"] = score(j)
        unique = {j["key"]: j for j in deduplicate(unique.values())}
    for j in unique.values():
        j["score"], j["lane"] = score(j)
        j["geography"] = geography(j["location"])
        j["expired"] = expired(j)
        j["screening_hold"] = screening_hold(j)
    db_path = Path(args.db);db_path.parent.mkdir(parents=True,exist_ok=True)
    cx = sqlite3.connect(db_path)
    cx.execute("CREATE TABLE IF NOT EXISTS jobs (job_key TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT, url TEXT, source TEXT, score INTEGER, first_seen TEXT, last_seen TEXT)")
    prior = {r[0] for r in cx.execute("SELECT job_key FROM jobs")}
    prior_urls = {r[0] for r in cx.execute("SELECT url FROM jobs")}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for j in unique.values():
        cx.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET score=excluded.score,last_seen=excluded.last_seen,url=excluded.url", (j["key"],j["title"],j["company"],j["location"],j["url"],j["source"],j["score"],stamp,stamp))
    if queries:
        cx.execute("CREATE TABLE IF NOT EXISTS engine_state (name TEXT PRIMARY KEY, value TEXT)")
        cx.execute("INSERT OR REPLACE INTO engine_state VALUES('query_offset', ?)",
                   (str(query_offset + source_stats['brave'].requests),))
    cx.commit();cx.close()
    all_passing = [j for j in unique.values() if j["score"] >= args.min_score]
    # A web-search hit can be a blog, overview, or expired board index.
    # Keep those leads visible but do not claim they are actual job records.
    eligible = [j for j in all_passing if not j["expired"] and not j["screening_hold"] and j["geography"] not in ("elsewhere", "remote_eligibility_unknown")
                and (j["source"] != "brave" or j["extraction_status"] == "jobposting_metadata")]
    selected = select(eligible, args.top)
    selected_keys = {j["key"] for j in selected}
    search_leads = select([j for j in all_passing if j["source"] == "brave" and j["extraction_status"] != "jobposting_metadata"], args.top)
    exploration = exploration_sample([j for j in unique.values() if j["key"] not in selected_keys and not j["expired"] and not j["screening_hold"] and j["geography"] != "elsewhere"
                                      and (j["score"] < args.min_score or j["geography"] in ("unknown", "remote_eligibility_unknown"))], args.exploration_slots)
    exploration_keys = {j["key"] for j in exploration}
    def decision(j):
        if j["expired"]: return "expired_by_source_date"
        if j["key"] in selected_keys: return "recommended"
        if j["source"] == "brave" and j["extraction_status"] != "jobposting_metadata": return "unverified_web_lead"
        if j["screening_hold"]: return j["screening_hold"]
        if j["key"] in exploration_keys: return "exploration_review"
        if j["geography"] in ("elsewhere", "remote_eligibility_unknown"): return "geography_review"
        if j["score"] < args.min_score: return "below_threshold"
        return "diversity_limit"
    decisions = {j["key"]: decision(j) for j in unique.values()}
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    lines=["# DC Career Discovery — "+stamp,"",f"**Candidates received:** {len(raw)} · **Unique:** {len(unique)} · **Recommended:** {len(selected)}", "", "Relevance scores are preliminary, not hiring probabilities. Posting status is not independently verified.", "", "| Score | Job | Employer | Location | Source | Status |","|---:|---|---|---|---|---|"]
    for j in selected:
        title=j["title"].replace("|","\\|")
        lines.append(f'| {j["score"]} | [{title}]({j["url"]}) | {j["company"]} | {j["location"]} | {j["sources"]} | {"Previously seen" if j["key"] in prior or j["url"] in prior_urls else "New"} |')
    lines += ["", "## Unverified web search leads",
              "These are **search results, not confirmed vacancies**. Visit and verify each before treating it as a job opening.",
              "",
              "| Preliminary score | Search result | Extraction status |",
              "|---:|---|---|"]
    for lead in search_leads:
        title = lead["title"].replace("|", "\\|")
        lines.append(f'| {lead["score"]} | [{title}]({lead["url"]}) | {lead["extraction_status"]} |')
    if not search_leads:
        lines.append("| — | No shortlisted unverified leads | — |")
    lines += ["", "## Exploration review — not recommendations",
              "Score-independent sample of unusual or uncertain candidates. All candidates remain in screening_audit.csv.",
              "| Role | Employer | Geography | Score |", "|---|---|---|---:|"]
    for j in exploration:
        title = j["title"].replace("|", "\\|")
        lines.append(f'| [{title}]({j["url"]}) | {j["company"]} | {j["geography"]} | {j["score"]} |')
    lines += ["", "## Source coverage", "| Source | Requests | Received | Malformed | Page cap reached | Status |",
              "|---|---:|---:|---:|---|---|"]
    for source, stats in source_stats.items():
        lines.append(f"| {source} | {stats.requests} | {stats.received} | {stats.malformed} | {stats.capped} | {stats.status} |")
    for source in skipped:
        lines.append(f"| {source} | 0 | 0 | 0 | — | not configured / not selected |")
    if queries:
        lines += ["", "### Executed search plan"] + [f"- {q.family}: {q.text}" for q in queries]
    lines += ["", "## Extraction audit",
              f"- Public HTML pages attempted: {sum(extraction_counts.values())}",
              f"- Structured JobPosting records obtained: {extraction_counts.get('jobposting_metadata', 0)}"]
    for status, count in sorted(extraction_counts.items()):
        lines.append(f"- {status}: {count}")
    lines += ["", "## Screening audit",
              f"- Preliminary threshold: {args.min_score}/100",
              f"- Candidates reaching threshold: {len(eligible)} of {len(unique)}",
              f"- Below threshold: {len(unique)-len(all_passing)}",
              f"- Search leads awaiting verification: {sum(v == 'unverified_web_lead' for v in decisions.values())}",
              f"- Geography review: {sum(v == 'geography_review' for v in decisions.values())}",
              f"- Seniority/background review: {sum(v in ('seniority_review', 'specialist_background_review') for v in decisions.values())}",
              f"- Expired by supplied closing date: {sum(j['expired'] for j in unique.values())}",
              f"- Exploration review slots filled: {len(exploration)}",
              f"- Invalid records rejected before screening: {invalid_records}",
              f"- At/above threshold but not selected after diversity rules: {len(eligible)-len(selected)}"]
    if not selected:
        lines.append("- No recommendations cleared screening; this is not evidence that no good jobs exist.")
    lines += ["", "### Highest-scoring screened-out candidates",
              "| Score | Title | Employer | Location | Reason |",
              "|---:|---|---|---|---|"]
    nonselected = [j for j in unique.values() if j["key"] not in selected_keys]
    for j in sorted(nonselected, key=lambda j:j["score"], reverse=True)[:12]:
        reason = decisions[j["key"]]
        def cell(v): return str(v).replace("|", "\\|").replace("\n", " ")
        lines.append(f'| {j["score"]} | {cell(j["title"])} | {cell(j["company"])} | {cell(j["location"])} | {reason} |')
    lines += ["", "## Retrieval issues"]+["- "+x for x in errors or ["None"]]
    target.write_text("\n".join(lines)+"\n",encoding="utf-8")
    with target.with_suffix(".csv").open("w",encoding="utf-8",newline="") as f:
        keys=("score","title","company","location","url","source","sources","source_id","lane","posted_at","snippet")
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for j in selected:w.writerow({k:j.get(k,"") for k in keys})
    with target.with_name("screening_audit.csv").open("w", encoding="utf-8", newline="") as f:
        keys = ("score", "title", "company", "location", "url", "source", "lane", "posted_at", "extraction_status", "sources", "source_id", "closing_at", "geography", "index_url", "discovery_urls", "raw_location", "retrieved_at", "evidence_status", "detail_status", "record_kind", "decision")
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for j in sorted(unique.values(), key=lambda x:x["score"], reverse=True):
            writer.writerow({**{k:j.get(k, "") for k in keys}, "decision":decisions[j["key"]]})
    for source, stats in source_stats.items():
        stats.unique = sum(source in j['sources'].split(',') for j in unique.values())
        stats.recommended = sum(source in j['sources'].split(',') for j in selected)
    metrics = {"received":len(raw), "unique":len(unique), "invalid":invalid_records,
               "duplicates":len(prepared)-len(unique), "extracted":extraction_counts.get("jobposting_metadata", 0),
               "above_threshold":len(eligible), "recommended":len(selected), "exploration":len(exploration),
               "sources":{name:vars(stats) for name,stats in source_stats.items()}, "not_configured":skipped,
               "decisions":{label:sum(v == label for v in decisions.values()) for label in sorted(set(decisions.values()))},
               "queries":[vars(q) for q in queries], "report":str(target), "errors":errors}
    target.with_name("discovery_metrics.json").write_text(json.dumps(metrics, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(metrics))
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())

