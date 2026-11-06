"""Small, provenance-required employer frames; no production employer allowlist."""
import csv
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime,timezone

from career_engine.ats import PublicTransport,FetchError,stamp
from career_engine.extract import safe_public_url

CBE_LAYER='https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Business_Licensing_and_Grants_WebMercator/MapServer/33'
CBE_ABOUT='https://hub.arcgis.com/datasets/dc299bc701f84f12b927eee9b3ec5a57_33/about'
# Deliberately omit contacts, owners, email, phone, address and coordinates.
CBE_FIELDS='OBJECTID,CERTIFICATIONNUMBER,BUSINESSNAME,WEBSITE,ORGANIZATIONTYPE,STARTDATE,EXPIRATIONDATE,GIS_LAST_MOD_DTTM'
REQUIRED=('frame_id','legal_name','source_url','source_observed_at','source_sha256','eligibility','footprint')


class FrameTransport(PublicTransport):
    def allowed(self,url):return url in (CBE_LAYER,CBE_LAYER+'/query')
    def redirect_allowed(self,original_host,target):return False


def validate_rows(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=5000:raise ValueError('Employer frame must contain 1–5000 rows')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k].strip() for k in REQUIRED):raise ValueError('Missing employer-frame provenance')
        if row['frame_id'] in seen:raise ValueError('Duplicate frame entity')
        if row['eligibility'] not in ('eligible','ineligible','unknown'):raise ValueError('Invalid eligibility')
        if not safe_public_url(row['source_url']) or not re.fullmatch(r'[a-f0-9]{64}',row['source_sha256']):raise ValueError('Invalid frame evidence')
        try:datetime.fromisoformat(row['source_observed_at'].replace('Z','+00:00'))
        except ValueError:raise ValueError('Invalid frame date') from None
        # A fixed non-personal schema prevents accidental import of contacts.
        normalized={k:row.get(k,'') for k in (*REQUIRED,'website_candidate','eligibility_basis','operating_name_claim','parent_affiliate_claim')}
        # Some directory WEBSITE values are actually contact addresses. Retain
        # the organization, but never import these addresses as site evidence.
        if '@' in str(normalized.get('website_candidate','')):normalized['website_candidate']=''
        result.append(normalized)
        seen.add(row['frame_id'])
    return result


def load_frame(path):
    path=Path(path)
    if path.stat().st_size>4_000_000:raise ValueError('Employer frame too large')
    if path.suffix.lower()=='.csv':
        with path.open(newline='',encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
        data={'schema_version':'employer-frame/v1','rows':validate_rows(rows)}
    else:
        data=json.loads(path.read_text());rows=data.get('rows') if isinstance(data,dict) else data
        data={'schema_version':'employer-frame/v1','rows':validate_rows(rows),'selection':data.get('selection',{}) if isinstance(data,dict) else {}}
    data['input_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def select_batch(frame,size=30,seed='issue25-development-v1'):
    if not 1<=size<=40:raise ValueError('Employer batch size must be 1–40')
    rows=frame['rows'];eligible=[r for r in rows if r['eligibility']=='eligible']
    ordered=sorted(eligible,key=lambda r:hashlib.sha256((seed+'|'+r['frame_id']).encode()).hexdigest())
    return {'schema_version':'employer-frame/v1','rows':ordered[:size],
            'selection':{'seed':seed,'rule':'SHA256(seed|frame_id), eligible rows only; no website, sector, title or platform selection',
                         'eligible_frame_entities':len(eligible),'selected':min(size,len(eligible)),
                         'input_sha256':frame.get('input_sha256'),'ineligible':sum(r['eligibility']=='ineligible' for r in rows),
                         'unknown_eligibility':sum(r['eligibility']=='unknown' for r in rows)}}


def acquire_cbe(transport,as_of=None,max_pages=4):
    """One documented public ArcGIS frame; finite window, fail on incomplete data."""
    as_of=as_of or datetime.now(timezone.utc);rows=[];evidence=[];offset=0
    for _ in range(max_pages):
        params={'f':'json','where':'1=1','outFields':CBE_FIELDS,'returnGeometry':'false','orderByFields':'OBJECTID ASC',
                'resultOffset':offset,'resultRecordCount':1000}
        text=transport.get(CBE_LAYER+'/query',params,max_bytes=2_000_000,kind='employer_directory')
        digest=hashlib.sha256(text.encode()).hexdigest();data=json.loads(text)
        if not isinstance(data,dict) or data.get('error') or not isinstance(data.get('features'),list):raise FetchError('frame_schema')
        observed=stamp();evidence.append({'url':CBE_LAYER+'/query','params':params,'observed_at':observed,'sha256':digest,'bytes':len(text.encode())})
        for f in data['features']:
            a=f.get('attributes',{});identifier=a.get('OBJECTID');name=a.get('BUSINESSNAME')
            if not isinstance(identifier,int) or not isinstance(name,str) or not name:raise FetchError('frame_entity_schema')
            start=a.get('STARTDATE');end=a.get('EXPIRATIONDATE');now=as_of.timestamp()*1000
            eligible='unknown' if not isinstance(start,(int,float)) or not isinstance(end,(int,float)) else 'eligible' if start<=now<=end else 'ineligible'
            site=str(a.get('WEBSITE') or '').strip()
            # Prefix only a domain published in the frame; never derive it from a name.
            if site and '://' not in site:site='https://'+site
            rows.append(dict(frame_id='dc-cbe:'+str(identifier),legal_name=name,website_candidate=site,
                        source_url=CBE_LAYER+'/query',source_observed_at=observed,source_sha256=digest,
                        eligibility=eligible,eligibility_basis=f'DSLBD listed CBE; STARTDATE={start}, EXPIRATIONDATE={end}; evaluated {as_of.isoformat()}',
                        footprint='Washington, DC headquarters per DSLBD CBE frame; job worksites unverified',
                        operating_name_claim='',parent_affiliate_claim=''))
        offset+=len(data['features'])
        if not data.get('exceededTransferLimit'):break
    else:raise FetchError('frame_page_cap_incomplete')
    normalized=validate_rows(rows)
    return {'schema_version':'employer-frame/v1','frame_source':CBE_ABOUT,'attribution':'Open Data DC / DSLBD, CC BY 4.0',
            'as_of':as_of.isoformat(),'source_requests':evidence,'rows':normalized,
            'scope':'DC certified-business headquarters frame only; not all employers, hiring status or market recall.'}
