"""Small geographical fixture boundary; no employers or candidate-fit rules."""
import re
from dataclasses import dataclass
from career_engine.discovery import geography

STATE_NAMES=dict(zip(
    'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split(),
    'Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming'.split('|')))


@dataclass(frozen=True)
class Market:
    city: str
    state: str
    identifier: str
    name: str

    @property
    def local_label(self):return 'dc_dmv' if self.identifier=='dc-dmv' else 'in_market'

    def label(self,location):
        text=str(location or '')
        if self.identifier=='dc-dmv':return 'dc_dmv' if re.fullmatch(r'(?i)\s*D\.?\s*C\.?\s*',text) else geography(text)
        normalized=re.sub(r'[^a-z0-9]+',' ',text.lower()).strip()
        city=re.sub(r'[^a-z0-9]+',' ',self.city.lower()).strip()
        states=(self.state.lower(),STATE_NAMES[self.state].lower())
        if any(re.search(r'\b'+re.escape(city)+r'\s+'+re.escape(state)+r'\b',normalized) for state in states):return 'in_market'
        general=geography(text)
        if general in ('unknown','us_remote','remote_eligibility_unknown'):return general
        if re.search(r'\bremote\b',text,re.I):return 'remote_eligibility_unknown'
        if normalized==city:return 'unknown'  # A city name without state can be ambiguous.
        return 'elsewhere'

    def asdict(self):return {'schema_version':'market/v1','id':self.identifier,'name':self.name,'city':self.city,'state':self.state,
        'scope_id':'dc-nearby-dmv' if self.identifier=='dc-dmv' else self.identifier,
        'other_DC_scope_boundaries':{'dc-strict':'District only; separate from nearby DMV','washington-msa':'Full Washington MSA is not implemented by this market fixture'} if self.identifier=='dc-dmv' else {},
        'scope':'DC plus existing nearby-MD/VA fixture' if self.identifier=='dc-dmv' else 'City/state label test; no inferred metro radius or remote eligibility.'}


DC_MARKET=Market('Washington','DC','dc-dmv','Washington DC / nearby DMV')


def city_market(city,state):
    if not isinstance(city,str) or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,79}",city) or str(state).upper() not in STATE_NAMES:raise ValueError('Use a US city and two-letter state')
    if city.lower()=='washington' and state.upper()=='DC':return DC_MARKET
    identifier=re.sub(r'[^a-z0-9]+','-',city.lower()).strip('-')+'-'+state.lower()
    return Market(city,state.upper(),identifier,city+', '+state.upper())
