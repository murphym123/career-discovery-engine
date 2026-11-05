"""Bounded, discovery-first job search with sqlite history."""
import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import requests
from career_engine.extract import enrich_public_page

QUERIES = [
    '"research associate" "Washington DC" policy',
    '"public affairs" "associate" "Washington DC"',
    '"government relations" "coordinator" Washington',
    '"program associate" think tank DC',
    '"political research" "Washington DC" hiring',
    '"communications associate" advocacy Washington',
    '"legislative correspondent" DC hiring',
    '"special assistant" policy Washington',
    '"operations associate" civic organization DC',
    '"strategy analyst" government affairs Washington',
    '"research and insights" associate Washington',
    '"development associate" advocacy DC',
    '"executive assistant" policy institute Washington',
    '"policy analyst" entry level "Washington DC"',
    '"community engagement" coordinator public affairs Washington',
    '"campaign research" associate Washington'
]
LANES = {
    "policy": ("policy", "legislative", "government affairs", "public affairs", "regulatory", "advocacy"),
    "research": ("research", "analysis", "analyst", "intelligence", "insights"),
    "operations": ("program", "operations", "coordinator", "special assistant", "executive assistant", "events", "outreach"),
    "communications": ("writing", "communications", "editorial", "content", "media", "press"),
    "campaigns": ("campaign", "political", "voter", "election", "fundraising")
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
    j = {k: str(raw.get(k, "") or "") for k in ("title", "company", "location", "url", "snippet", "description", "source", "posted_at")}
    j["url"] = canonical(j["url"])
    if not j["url"]:
        return None
    identity = "|".join(norm(j[k]) for k in ("company", "title", "location")) if j["company"] and j["title"] else j["url"]
    j["key"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return j

def score(j):
    title = norm(j["title"])
    body = norm(" ".join((j["title"], j["snippet"], j["description"][:1500])))
    lane_scores = {name: sum(12 if phrase in title else 3 if phrase in body else 0 for phrase in phrases) for name, phrases in LANES.items()}
    lane = max(lane_scores, key=lane_scores.get)
    value = lane_scores[lane] + min(18, 3 * sum(x in body for x in ("policy", "research", "political", "public affairs", "communications", "program", "government")))
    location = norm(j["location"])
    if any(p in location for p in ("washington", "arlington", "bethesda", "alexandria")): value += 18
    elif "remote" in location or "united states" in location: value += 7
    elif location and "worldwide" not in location: value -= 12
    if any(x in title for x in ("associate", "assistant", "coordinator", "junior", "entry level", "analyst", "correspondent")): value += 10
    if any(x in title for x in SENIOR): value -= 50
    if any(x in body for x in ("phd required", "doctoral degree required", "10 years of experience")): value -= 24
    return max(0, min(value, 100)), lane

def select(jobs, top):
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
        yield dict(title=j.get("title",""),company=j.get("company_name",""),location=j.get("candidate_required_location","Remote"),url=j.get("url",""),snippet=j.get("category",""),description=re.sub("<[^>]*>", " ", j.get("description",""))[:10000], source="remotive",posted_at=j.get("publication_date",""))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sources", default="remotive", help="remotive,brave,csv")
    p.add_argument("--import-csv")
    p.add_argument("--query-budget", type=int, default=6)
    p.add_argument("--results-per-query", type=int, default=20)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-score", type=int, default=22)
    p.add_argument("--max-extract", type=int, default=0, help="Max distinct public HTML pages to inspect (0 disables)")
    p.add_argument("--db", default="data/live.db")
    p.add_argument("--output", default="output/live_brief.md")
    args = p.parse_args()
    if not 1<=args.query_budget<=20 or not 1<=args.results_per_query<=20 or not 0<=args.max_extract<=30:
        p.error("invalid search or extraction budget")
    raw, errors = [], []
    s = requests.Session()
    for source in args.sources.split(","):
        try:
            if source == "remotive": raw.extend(remotive(s))
            elif source == "brave":
                import time
                for q in QUERIES[:args.query_budget]:
                    try: raw.extend(brave(s,q,args.results_per_query))
                    except requests.RequestException as e: errors.append("Brave query: "+type(e).__name__)
                    time.sleep(0.25)
            elif source == "csv" and args.import_csv:
                with open(args.import_csv,encoding="utf-8-sig",newline="") as f: raw.extend(csv.DictReader(f))
            else: errors.append("Unsupported source: "+source)
        except (requests.RequestException, RuntimeError, OSError, ValueError) as e:
            errors.append(source+": "+str(e)[:180])
    unique = {}
    for item in raw:
        j = clean(item)
        if j:
            j["score"],j["lane"] = score(j)
            j["extraction_status"] = "source_description" if j["description"] else "not_attempted"
            if j["key"] not in unique or j["score"] > unique[j["key"]]["score"]:
                unique[j["key"]] = j

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
        again = {}
        for j in unique.values():
            if j["key"] not in again or j["score"] > again[j["key"]]["score"]:
                again[j["key"]] = j
        unique = again
    db_path = Path(args.db);db_path.parent.mkdir(parents=True,exist_ok=True)
    cx = sqlite3.connect(db_path)
    cx.execute("CREATE TABLE IF NOT EXISTS jobs (job_key TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT, url TEXT, source TEXT, score INTEGER, first_seen TEXT, last_seen TEXT)")
    prior = {r[0] for r in cx.execute("SELECT job_key FROM jobs")}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for j in unique.values():
        cx.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET score=excluded.score,last_seen=excluded.last_seen,url=excluded.url", (j["key"],j["title"],j["company"],j["location"],j["url"],j["source"],j["score"],stamp,stamp))
    cx.commit();cx.close()
    eligible = [j for j in unique.values() if j["score"] >= args.min_score]
    selected = select(eligible, args.top)
    selected_keys = {j["key"] for j in selected}
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    lines=["# DC Career Discovery — "+stamp,"",f"**Candidates received:** {len(raw)} · **Unique:** {len(unique)} · **Recommended:** {len(selected)}", "", "Relevance scores are preliminary, not hiring probabilities. Posting status is not independently verified.", "", "| Score | Job | Employer | Location | Source | Status |","|---:|---|---|---|---|---|"]
    for j in selected:
        title=j["title"].replace("|","\\|")
        lines.append(f'| {j["score"]} | [{title}]({j["url"]}) | {j["company"]} | {j["location"]} | {j["source"]} | {"Previously seen" if j["key"] in prior else "New"} |')
    lines += ["", "## Extraction audit",
              f"- Public HTML pages attempted: {sum(extraction_counts.values())}",
              f"- Structured JobPosting records obtained: {extraction_counts.get('jobposting_metadata', 0)}"]
    for status, count in sorted(extraction_counts.items()):
        lines.append(f"- {status}: {count}")
    lines += ["", "## Screening audit",
              f"- Preliminary threshold: {args.min_score}/100",
              f"- Candidates reaching threshold: {len(eligible)} of {len(unique)}",
              f"- Below threshold: {len(unique)-len(eligible)}",
              f"- At/above threshold but not selected after diversity rules: {len(eligible)-len(selected)}"]
    if not selected:
        lines.append("- No recommendations cleared screening; this is not evidence that no good jobs exist.")
    lines += ["", "### Highest-scoring screened-out candidates",
              "| Score | Title | Employer | Location | Reason |",
              "|---:|---|---|---|---|"]
    nonselected = [j for j in unique.values() if j["key"] not in selected_keys]
    for j in sorted(nonselected, key=lambda j:j["score"], reverse=True)[:12]:
        reason = "Below threshold" if j["score"] < args.min_score else "Diversification cap"
        def cell(v): return str(v).replace("|", "\\|").replace("\n", " ")
        lines.append(f'| {j["score"]} | {cell(j["title"])} | {cell(j["company"])} | {cell(j["location"])} | {reason} |')
    lines += ["", "## Retrieval issues"]+["- "+x for x in errors or ["None"]]
    target.write_text("\n".join(lines)+"\n",encoding="utf-8")
    with target.with_suffix(".csv").open("w",encoding="utf-8",newline="") as f:
        keys=("score","title","company","location","url","source","lane","posted_at","snippet")
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for j in selected:w.writerow({k:j.get(k,"") for k in keys})
    with target.with_name("screening_audit.csv").open("w", encoding="utf-8", newline="") as f:
        keys = ("score", "title", "company", "location", "url", "source", "lane", "posted_at", "extraction_status", "decision")
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for j in sorted(unique.values(), key=lambda x:x["score"], reverse=True):
            decision = "recommended" if j["key"] in selected_keys else ("below_threshold" if j["score"] < args.min_score else "diversity_limit")
            writer.writerow({**{k:j.get(k, "") for k in keys}, "decision":decision})
    print(json.dumps({"received":len(raw),"unique":len(unique),"extracted":extraction_counts.get("jobposting_metadata", 0),"above_threshold":len(eligible),"recommended":len(selected),"report":str(target),"errors":errors}))
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
