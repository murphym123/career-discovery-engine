"""Public regional discovery across offices and employers, without ranking.

Only advertised public pages are read: no search APIs, feeds disallowed by
robots, login, apply endpoints, employer allowlists, or paid services.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
import time
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import requests
from career_engine.extract import robots_allowed, safe_public_url, USER_AGENT
from career_engine.sources import SourceStats

DC_SOURCES = ("senate", "unionjobs")  # Stable legacy alias for existing callers.
ALL_DC_SOURCES = (*DC_SOURCES,"globaljobs","onthinktanks","workfornonprofits")
INDEXES = {
    "senate": ("https://careers.employment.senate.gov/l-district-of-columbia-jobs.html",),
    "unionjobs": tuple("https://www.unionjobs.com/location.php?state=" + state
                       for state in ("District%20of%20Columbia", "Maryland", "Virginia")),
    "globaljobs": ("https://globaljobs.org/",),
    "onthinktanks": ("https://onthinktanks.org/jobsboard/",),
    "workfornonprofits": ("https://workfornonprofits.org/jobs/district-of-columbia",),
}
HOSTS = {"senate": {"careers.employment.senate.gov"},
         "unionjobs": {"www.unionjobs.com", "unionjobs.com"},
         "globaljobs": {"globaljobs.org","www.globaljobs.org"},
         "onthinktanks": {"onthinktanks.org"},
         "workfornonprofits": {"workfornonprofits.org"}}
MAX_BYTES = 1_000_000
UNION_RSS = "https://www.unionjobs.com/rss.php"
GLOBAL_RSS = "https://globaljobs.org/jobs/feed.rss"


def listing_url(source,url):
    try:parts=urlsplit(url)
    except ValueError:return False
    if not safe_public_url(url) or parts.hostname not in HOSTS[source]:
        return False
    if source in ("globaljobs","onthinktanks","workfornonprofits") and parts.query:return False
    if source=="globaljobs":return bool(re.fullmatch(r"/jobs/\d+-[a-zA-Z0-9_-]+",parts.path))
    if source=="onthinktanks":return bool(re.fullmatch(r"/job/[a-zA-Z0-9_-]+/?",parts.path))
    if source=="workfornonprofits":return bool(re.fullmatch(r"/companies/[a-zA-Z0-9_-]+/jobs/[a-zA-Z0-9_-]+/?",parts.path))
    return bool(re.fullmatch(r"/job/[a-zA-Z0-9_-]+",parts.path)) if source=="senate" else bool(
        re.fullmatch(r"/listing/\d+[a-zA-Z0-9_-]*",parts.path) or
        (parts.path=="/listing.php" and re.fullmatch(r"id=\d+",parts.query)))


class Node:
    def __init__(self, tag="root", attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        if self.tag in ("script", "style", "svg"):
            return ""
        return re.sub(r"\s+", " ", " ".join(x.text() if isinstance(x, Node) else x for x in self.children)).strip()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def find(self, predicate):
        return next((node for node in self.walk() if predicate(node)), None)

    def closest(self, tag):
        node = self.parent
        while node and node.tag != tag:
            node = node.parent
        return node


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_index(source, html, index_url):
    """Retain every visible listing; geography is evidence, not a title gate."""
    doc = Document(html).root
    records = []
    if source == "senate":
        for link in doc.walk():
            if link.attrs.get("data-testid") != "jobs-list-item-link":
                continue
            card = link.closest("li")
            if card is None:
                continue
            location = card.find(lambda n: n.has_class("location"))
            parts = [n.text() for n in location.children if isinstance(n, Node)] if location else []
            records.append(dict(title=link.text(), company=parts[0] if len(parts)>1 else "",
                                location=parts[-1] if parts else "", url=urljoin(index_url,link.attrs.get("href", "")),
                                source_id=link.attrs.get("data-id", ""), snippet=card.text()))
    elif source == "unionjobs":
        for org in (n for n in doc.walk() if n.has_class("organization")):
            heading = org.find(lambda n: n.tag == "h3")
            for li in (n for n in org.walk() if n.tag == "li"):
                link = li.find(lambda n: n.tag == "a" and bool(re.match(r"/(listing/\d|index.php\?zone=/unionactive/view_article.cfm)",n.attrs.get("href", ""))))
                if link is None:
                    continue
                title, text = link.text(), li.text()
                suffix = text[len(title):].strip(" ,") if text.startswith(title) else ""
                location = re.split(r"\(Posted:", suffix, maxsplit=1)[0].strip()
                posted = re.search(r"\(Posted:\s*([0-9/]+)\)", suffix)
                url=urljoin(index_url,link.attrs["href"])
                records.append(dict(title=title,company=heading.text() if heading else "",location=location,
                                    url=url,source_id=re.search(r"listing/(\d+)",url).group(1) if "/listing/" in url else url,
                                    snippet=text,posted_at=posted.group(1) if posted else "",
                                    record_kind="talent_pool" if heading and "resume posting service" in heading.text().lower() else "advertised_position"))
    elif source in ("globaljobs","onthinktanks","workfornonprofits"):
        from career_engine.specialty_sources import parse_specialty_index
        records=parse_specialty_index(source,html,index_url)
    else:raise ValueError("unsupported DC source")
    unique = {}
    for record in records:
        if record["title"] and listing_url(source,record["url"]):
            record.update(source=source,index_url=index_url,raw_location=record["location"],
                          evidence_status="board_index_record",detail_status="not_requested")
            unique.setdefault(record["url"],record)
    return list(unique.values())


def parse_detail(source, html):
    fields,status=_parse_detail(source,html)
    if status=="public_description_observed":
        from career_engine.ats_registry import observed_ats_urls
        fields["employer_board_urls"]=observed_ats_urls(html)
    return fields,status


def _parse_detail(source, html):
    if source in ("globaljobs","onthinktanks","workfornonprofits"):
        from career_engine.specialty_sources import parse_specialty_detail
        return parse_specialty_detail(source,html)
    doc = Document(html).root
    if source == "senate":
        # The specific job's description, never related jobs or navigation.
        body = doc.find(lambda n: n.attrs.get("data-testid") == "job-description")
        if body is None:
            body = doc.find(lambda n: n.has_class("job-description"))
    elif source == "unionjobs":
        body = doc.find(lambda n: n.attrs.get("id") == "job-details")
        if body is None:
            body = doc.find(lambda n: n.attrs.get("id") == "content")
    else:
        raise ValueError("unsupported DC source")
    if body is None:
        return {}, "detail_schema_changed"
    description = body.text()
    if not description:
        return {}, "empty_description"
    if re.search(r"position has been filled|job is no longer (available|active)|no longer accepting applications",description,re.I):
        return {},"closed_notice_observed"
    return {"description":description[:10000]}, "public_description_observed"


def parse_union_rss(xml):
    """The board's advertised public RSS, restricted by geographic metadata.

    It covers the newest 50 national listings; regional indexes fill that gap.
    RSS state tags do not prove a job has a DC worksite.
    """
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise ValueError("unsupported XML declaration")
    root=ElementTree.fromstring(xml)
    rows=[]
    for item in root.findall("./channel/item"):
        title,_,states=(item.findtext("title") or "").rpartition(" : ")
        if not {state.strip() for state in states.split(",")} & {"District of Columbia","Maryland","Virginia"}:
            continue
        body=Document(item.findtext("description") or "").root
        company=body.find(lambda n:n.tag=="strong")
        text=body.text()
        location=re.split(r"Posted:",text.split(company.text(),1)[-1] if company else text,maxsplit=1)[0].strip(" ,")
        url=(item.findtext("link") or "").replace("http://", "https://",1)
        identity=re.search(r"[?&]id=(\d+)",url)
        if not identity or not listing_url("unionjobs",url):
            continue
        rows.append(dict(title=title,company=company.text() if company else "",location=location,raw_location=location,
                         url=url,source_id=identity.group(1),source="unionjobs",snippet=text,
                         posted_at=item.findtext("pubDate") or "",index_url=UNION_RSS,
                         evidence_status="board_feed_record",detail_status="not_requested"))
    return rows


class _CountingSession:
    def __init__(self, session, stats):
        self.session,self.stats=session,stats

    def get(self, *args, **kwargs):
        self.stats.requests += 1
        return self.session.get(*args,**kwargs)


def _fetch(session, source, url, cache, pause, xml=False):
    for _ in range(3):
        parts=urlsplit(url)
        is_index=url in INDEXES[source] or (source=="unionjobs" and url==UNION_RSS) or (source=="globaljobs" and url==GLOBAL_RSS)
        if not (is_index or listing_url(source,url)):
            return "", "unsafe_url"
        if not robots_allowed(session,url,cache):
            return "", "robots_disallowed_or_unavailable"
        pause(1)
        response=session.get(url,headers={"User-Agent":USER_AGENT,"Accept":"text/html"},timeout=15,allow_redirects=False,stream=True)
        if response.status_code in (301,302,303,307,308):
            target=response.headers.get("Location", "")
            response.close()
            if not target:
                return "", "redirect_without_target"
            url=urljoin(url,target)
            continue
        if response.status_code != 200:
            response.close()
            return "",f"http_{response.status_code}"
        try:
            mime=response.headers.get("Content-Type", "").split(";")[0].strip()
            if mime not in (("application/rss+xml","application/xml","text/xml","text/html", "") if xml else ("text/html","application/xhtml+xml", "")):
                return "", "not_html"
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    return "", "page_too_large"
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8",errors="replace"),"ok"
        finally:
            response.close()
    return "", "too_many_redirects"


def retrieve_dc(session, source, max_details=20, stats=None, pause=time.sleep, strategy="diverse",seed=0):
    """Bounded stratified details; index records survive all detail failures.

    Senate's client-only Load more and robots-disallowed search/feed endpoints
    are deliberately not called. Union Jobs' three regional indexes are swept.
    """
    if source not in ALL_DC_SOURCES or not 0<=max_details<=30:
        raise ValueError("invalid DC source or detail budget")
    stats=stats if stats is not None else SourceStats()
    transport=_CountingSession(session,stats)
    cache,unique={},{}
    source_blocked=False
    stamp=datetime.now(timezone.utc).isoformat(timespec="seconds")
    if source=="unionjobs":
        try:
            xml,status=_fetch(transport,source,UNION_RSS,cache,pause,xml=True)
            if status=="ok":
                stats.pages+=1
                rows=parse_union_rss(xml)
                stats.received+=len(rows)
                for row in rows:
                    row["retrieved_at"]=stamp
                    unique[row["source_id"]]=row
            else:
                stats.errors.append("RSS "+status)
                source_blocked=status in ("http_403","http_429")
        except (requests.RequestException,ValueError,ElementTree.ParseError):
            stats.errors.append("RSS network_or_parse_error")
    elif source=="globaljobs":
        from career_engine.specialty_sources import parse_global_feed
        try:
            xml,status=_fetch(transport,source,GLOBAL_RSS,cache,pause,xml=True)
            if status=="ok":
                stats.pages+=1
                rows=parse_global_feed(xml)
                stats.received+=len(rows)
                for row in rows:
                    if not listing_url(source,row["url"]):continue
                    row["retrieved_at"]=stamp
                    unique[row["source_id"]]=row
            else:
                stats.errors.append("RSS "+status)
                source_blocked=status in ("http_403","http_429")
        except (requests.RequestException,ValueError,ElementTree.ParseError):
            stats.errors.append("RSS network_or_parse_error")
    for url in INDEXES[source]:
        if source_blocked:
            break
        try:
            html,status=_fetch(transport,source,url,cache,pause)
        except requests.RequestException:
            html,status="","network_error"
        if status!="ok":
            stats.errors.append("Index "+status)
            if status in ("http_429","http_403"):
                source_blocked=True
                break
            continue
        stats.pages+=1
        rows=parse_index(source,html,url)
        stats.received+=len(rows)
        if not rows:
            stats.errors.append("No listing cards parsed; source may have changed")
        for row in rows:
            row["retrieved_at"]=stamp
            # Regional card provides the clean landing URL for a feed identity.
            previous=unique.setdefault(row["source_id"],{})
            origins=set(previous.get("discovery_urls",previous.get("index_url", "")).split(" ; "))
            # An absolute RSS date must not be overwritten by 'Yesterday'.
            if previous.get("posted_at") and not row.get("posted_at"):
                row["posted_at"]=previous["posted_at"]
            previous.update(row)
            previous["discovery_urls"]=" ; ".join(sorted((origins|{url})-{""}))
    if source=="senate":
        stats.capped=True  # Public rendered batch only; no assertion of exhaustive retrieval.
    stats.unique=len(unique)
    if source_blocked:
        max_details=0
    from career_engine.inspection import plan_inspections
    records=list(unique.values())
    plan=plan_inspections(records,max_details,seed,strategy)
    by_id={row["source"]+":"+row["source_id"]:row for row in records}
    for row in records:
        row["inspection_stratum"]="not_selected_budget"
    detail_attempts=0
    for choice in plan:
        record=by_id[choice["identity"]]
        record["inspection_stratum"]=choice["stratum"]
        if not source_blocked:
            detail_attempts+=1
            try:
                html,status=_fetch(transport,source,record["url"],cache,pause)
                fields,status=parse_detail(source,html) if status=="ok" else ({},status)
            except requests.RequestException:
                fields,status={},"network_error"
            record.update(fields,detail_status=status)
            if status=="public_description_observed":
                record["evidence_status"]="board_detail_observed_not_employer_confirmed"
            else:
                stats.errors.append("Detail "+status)
            # On rate limit or denial, keep records without making more detail requests.
            if status in ("http_429","http_403"):
                source_blocked=True
        else:
            record["detail_status"]="not_attempted_source_blocked"
    for record in records:
        yield record
    stats.capped=stats.capped or len(unique)>detail_attempts
    stats.inspection_strategy=strategy
    stats.details_planned=len(plan)
    stats.details_attempted=detail_attempts
    stats.descriptions_observed=sum(bool(row.get("description")) for row in records)
    stats.index_only=len(records)-stats.descriptions_observed
    stats.status="partial_failure" if stats.errors and unique else "failed" if stats.errors else "ok"


def observe_selected_details(session,records,identities,max_details=10,pause=time.sleep):
    """Human-selected source QA by observed IDs; independent of job scores.

    Only enrich records already captured from the boards. Intended for reviewing
    requirements in an explicit stratified sample beyond a sequential page cap.
    """
    if not 0<=max_details<=30 or len(identities)>max_details:
        raise ValueError("invalid selected-detail budget")
    available={row["source"]+":"+row["source_id"]:row for row in records}
    if any(identity not in available for identity in identities):
        raise ValueError("selected identity absent from captured board records")
    stats,cache=SourceStats(),{}
    transport=_CountingSession(session,stats)
    stopped=set()
    for identity in identities:
        row=available[identity]
        if row["source"] in stopped:
            continue
        try:
            html,status=_fetch(transport,row["source"],row["url"],cache,pause)
            fields,status=parse_detail(row["source"],html) if status=="ok" else ({},status)
        except requests.RequestException:
            fields,status={},"network_error"
        row.update(fields,detail_status=status)
        if status=="public_description_observed":
            row["evidence_status"]="board_detail_observed_not_employer_confirmed"
        else:
            stats.errors.append(identity+": "+status)
        if status in ("http_403","http_429"):
            stopped.add(row["source"])
    return stats
