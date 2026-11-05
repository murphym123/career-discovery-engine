"""Visible public specialty boards: no private APIs, accounts, or employer lists."""
import json
import re
from datetime import datetime
from urllib.parse import urljoin
from xml.etree import ElementTree

from career_engine.dc_sources import Document
from career_engine.extract import parse_jobposting,strip_html

SPECIALTY_SOURCES=("globaljobs","onthinktanks","workfornonprofits")
GLOBAL_FEED="https://globaljobs.org/jobs/feed.rss"


def _text(node,cls):
    found=node.find(lambda n:n.has_class(cls))
    return found.text() if found else ""


def parse_specialty_index(source,html,index_url):
    doc=Document(html).root
    rows=[]
    if source=="globaljobs":
        for card in (n for n in doc.walk() if n.has_class("atlas-job-row")):
            title_node=card.find(lambda n:n.has_class("atlas-job-title"))
            link=title_node.find(lambda n:n.tag=="a") if title_node else None
            if link is None:continue
            experience=card.attrs.get("data-experience","")
            hints=[]
            if re.search(r"\b1\b",experience):hints.append("provider_internship")
            if re.search(r"\b2\b",experience):hints.append("provider_early_career")
            if re.search(r"\b3\b",experience):hints.append("provider_mid_career")
            if re.search(r"\b4\b",experience):hints.append("provider_advanced")
            rows.append(dict(title=link.text(),company=_text(card,"atlas-job-org"),
                             location=_text(card,"atlas-job-location"),url=urljoin(index_url,link.attrs.get("href","")),
                             source_id=card.attrs.get("id","").removeprefix("job_div_"),
                             posted_at_raw=_text(card,"atlas-job-date"),snippet=card.text(),
                             experience_hint=",".join(hints),provider_sector=card.attrs.get("data-sector", ""),
                             work_arrangement=card.attrs.get("data-location_type", "")))
    elif source=="onthinktanks":
        for card in (n for n in doc.walk() if n.tag=="article" and n.has_class("c-card")):
            link=card.find(lambda n:n.tag=="a" and "/job/" in n.attrs.get("href",""))
            if link is None:continue
            meta={}
            for group in (n for n in card.walk() if n.has_class("c-card__meta-group")):
                value=_text(group,"c-card__meta-desc")
                label=group.text().removesuffix(value).strip().lower()
                meta[label]=value
            rows.append(dict(title=_text(card,"c-card__title"),company=meta.get("organisation",""),
                             location=meta.get("location",""),url=link.attrs["href"],
                             source_id=link.attrs["href"].rstrip("/").split("/")[-1],
                             closing_at_raw=meta.get("application deadline", ""),snippet=card.text(),
                             sector_hint="think_tank"))
    elif source=="workfornonprofits":
        for card in (n for n in doc.walk() if n.tag=="article" and n.attrs.get("data-kardow-dynamic")=="jobs"):
            link=card.find(lambda n:n.tag=="a" and bool(re.fullmatch(r"/companies/[^/]+/jobs/[^/]+",n.attrs.get("href",""))))
            title=card.find(lambda n:n.tag=="h2")
            if link is None or title is None:continue
            company=card.find(lambda n:n.tag=="a" and bool(re.fullmatch(r"/companies/[^/]+",n.attrs.get("href",""))))
            location=card.find(lambda n:n.attrs.get("aria-label","").startswith("Filter by location "))
            category=card.find(lambda n:n.attrs.get("aria-label","").startswith("Filter by category "))
            jobtype=card.find(lambda n:n.attrs.get("aria-label","").startswith("Filter by job type "))
            posted=card.find(lambda n:n.tag=="time")
            href=link.attrs["href"]
            rows.append(dict(title=title.text(),company=company.text() if company else "",
                             location=location.text() if location else "",url=urljoin(index_url,href),
                             source_id=href.rsplit("-",1)[-1],category=category.text() if category else "",
                             employment_type=jobtype.text() if jobtype else "",posted_at_raw=posted.text() if posted else "",
                             snippet=card.text(),sector_hint="nonprofit"))
    else:raise ValueError("unsupported specialty source")
    for row in rows:
        row.update(source=source,index_url=index_url,raw_location=row["location"],
                   evidence_status="board_index_record",detail_status="not_requested",record_kind="advertised_position")
        if row.get("closing_at_raw"):
            try:row["closing_at"]=datetime.strptime(row["closing_at_raw"],"%d %B %Y").date().isoformat()
            except ValueError:pass
    return rows


def parse_global_feed(xml):
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():raise ValueError("unsupported XML declaration")
    root=ElementTree.fromstring(xml)
    rows=[]
    for item in root.findall("./channel/item"):
        url=item.findtext("link") or ""
        identity=re.search(r"/jobs/(\d+)-",url)
        if not identity:continue
        title,sep,location=(item.findtext("title") or "").rpartition(" job in ")
        if not sep:title,location=item.findtext("title") or "",""
        rows.append(dict(source="globaljobs",source_id=identity.group(1),title=title,company="",location=location,
                         raw_location=location,url=url,index_url=GLOBAL_FEED,posted_at=item.findtext("pubDate") or "",
                         snippet=Document(item.findtext("description") or "").root.text(),record_kind="advertised_position",
                         detail_status="not_requested",evidence_status="board_feed_record"))
    return rows


def parse_specialty_detail(source,html):
    doc=Document(html).root
    fields={}
    if source=="workfornonprofits":
        # Public JobPosting schema, not application state or protected account APIs.
        fields=parse_jobposting(html) or {}
        for node in doc.walk():
            if node.tag!="script" or node.attrs.get("type")!="application/ld+json":continue
            try:obj=json.loads("".join(c for c in node.children if isinstance(c,str)))
            except (ValueError,TypeError):continue
            if not isinstance(obj,dict) or obj.get("@type")!="JobPosting":continue
            full=strip_html(obj.get("description", ""))
            fields["description"]=full[:20000]
            fields["description_truncated"]=len(full)>20000
            if obj.get("validThrough"):fields["closing_at"]=str(obj["validThrough"])
        if fields.get("description"):
            return fields,"public_description_observed"
        return {},"detail_schema_changed"
    if source=="globaljobs":
        body=doc.find(lambda n:n.has_class("prose-job-description"))
    elif source=="onthinktanks":
        body=doc.find(lambda n:n.has_class("o-content-from-editor"))
        if body is None:body=doc.find(lambda n:n.tag=="main" and n.has_class("c-single--job"))
    else:raise ValueError("unsupported specialty source")
    if body is None:return {},"detail_schema_changed"
    description=body.text()
    if re.search(r"position has been filled|job is no longer (available|active)|no longer accepting applications",description,re.I):
        return {},"closed_notice_observed"
    if not description:return {},"empty_description"
    return dict(description=description[:20000],description_truncated=len(description)>20000),"public_description_observed"
