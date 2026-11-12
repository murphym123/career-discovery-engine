"""Optional USAJOBS handoff for the manual DC regional expansion only."""
from dataclasses import asdict
import hashlib
import json
import os
import time
from urllib.parse import urlsplit

import requests

from career_engine.ats import stamp
from career_engine.extract import safe_public_url
from career_engine.sources import SourceStats,retrieve,REQUIREMENTS
from career_engine.worksite_evidence import worksite_evidence

ENDPOINT='https://data.usajobs.gov/api/search'
SETUP='Request an authorized key at https://developer.usajobs.gov/apirequest/; set USAJOBS_API_KEY and USAJOBS_USER_AGENT (the registered email) as repository secrets; confirm authorized use/terms with repository variable USAJOBS_TERMS_ACCEPTED=true.'


class BoundedSession:
    """Keep the existing collector, but bound streaming and retain public evidence.

    Headers and arbitrary exception text never enter outputs. Credentials may go
    only to the fixed HTTPS endpoint; redirects are never followed.
    """
    def __init__(self,session=None,pause=time.sleep):
        self.session=session if session is not None else requests.Session()
        self.capture_body=None
        self.requests=[];self.raw={};self.bytes=0;self.total=None;self.stopped=False;self.pause=pause;self.started=time.monotonic()
    def get(self,url,params=None,headers=None,**kwargs):
        if url!=ENDPOINT or self.stopped:raise ValueError('usajobs_endpoint_or_stopped')
        if self.bytes>=64_000_000 or time.monotonic()-self.started>=600:raise ValueError('usajobs_global_budget')
        self.pause(1)
        item={'url':url,'params':params,'retrieved_at':stamp()};self.requests.append(item)
        response=self.session.get(url,params=params,headers=headers,timeout=25,allow_redirects=False,stream=True)
        try:
            item['status']=response.status_code
            if response.status_code!=200:
                self.stopped=True
                return response  # retrieve() reads only status for non-200.
            body=bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk);self.bytes+=len(chunk)
                if len(body)>8_000_000 or self.bytes>64_000_000 or time.monotonic()-self.started>=600:
                    item['error']='response_too_large';self.stopped=True;raise ValueError('usajobs_response_too_large')
            item['bytes'] = len(body)
            withheld = self.capture_body is not None and self.capture_body(bytes(body))
            if withheld:
                item['capture_withheld'] = 'secret_echo'
            else:
                item['sha256'] = hashlib.sha256(body).hexdigest()
            data=json.loads(body)
            result=data.get('SearchResult',{}) if isinstance(data,dict) else {}
            self.total=result.get('SearchResultCountAll')
            if withheld and type(self.total) is not int:
                self.total = None
            if not withheld:
                for row in result.get('SearchResultItems',[]) or []:
                    if isinstance(row,dict):self.raw[str(row.get('MatchedObjectId',''))]=row
            class Parsed:
                status_code=200
                prospect_captured=self.capture_body is not None
                def json(self):return data
            return Parsed()
        finally:response.close()


def collect_usajobs(max_pages=6,session=None,pause=time.sleep):
    if not 1<=max_pages<=20:raise ValueError('USAJOBS page budget must be 1–20')
    missing=[k for k in REQUIREMENTS['usajobs'] if not os.getenv(k,'').strip()]
    terms=os.getenv('USAJOBS_TERMS_ACCEPTED','').lower()=='true'
    config={'configured':not missing,'terms_confirmed':terms,'missing_configuration':missing,
            'setup_requirements':SETUP,'affiliation':'Unofficial research tool; not operated, sponsored, approved or endorsed by OPM.'}
    if missing or not terms:
        if os.getenv('PROSPECT_DATA_DIR'):
            from career_engine.store.collectors import record_usajobs_skip
            record_usajobs_skip('missing_credentials' if missing else 'terms_unconfirmed', max_pages)
        errors=(['Missing configuration: '+', '.join(missing)] if missing else [])
        if not terms:errors.append('Confirm authorized API use: USAJOBS_TERMS_ACCEPTED=true')
        return [],{**asdict(SourceStats(status='not_collected')),**config,'errors':errors}
    bounded=BoundedSession(session,pause);counts=SourceStats();rows=[];seen=set()
    try:
        for row in retrieve(bounded,'usajobs',max_pages,counts):
            if not row['source_id'] or not safe_public_url(row['url']) or urlsplit(row['url']).hostname not in ('www.usajobs.gov','usajobs.gov'):
                counts.malformed+=1;continue
            if row['source_id'] in seen:continue
            seen.add(row['source_id'])
            row.update(identity='usajobs:'+row['source_id'],index_url=ENDPOINT,retrieved_at=stamp(),
                       detail_status='api_description_observed' if row.get('description') else 'index_only',
                       record_kind='advertised_position',raw_provider_record=bounded.raw.get(row['source_id'],{}),
                       evidence_status='USAJOBS_API_record_observed_not_independently_verified_accepting_applications')
            row.update(worksite_evidence(row));rows.append(row)
    except Exception as exc:
        counts.errors.append('source_adapter_exception_'+type(exc).__name__);counts.status='failed'
    counts.unique=len(rows)
    if rows and counts.status!='ok':counts.status='partial_failure'
    try:total=int(bounded.total)
    except (ValueError,TypeError):total=None
    inventory={'scope':'USAJOBS Washington, DC radius 35 miles; newest open-date order; bounded API result window',
               'location_name':'Washington, DC','radius_miles':35,'maximum_pages':max_pages,'results_per_page':100,
               'provider_claimed_total':bounded.total,'metadata_retained':len(rows),
               'metadata_complete':total is not None and total==len(rows) and not counts.capped and counts.status=='ok' and not counts.malformed,
               'note':'API response total/window only, not all federal jobs or all DC employment. Descriptions are normalized excerpts; raw public API fields retained.'}
    return rows,{**asdict(counts),**config,'transport':{'requests':bounded.requests,'bytes':bounded.bytes},'inventory':inventory}
