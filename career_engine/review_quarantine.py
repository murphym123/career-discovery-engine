"""Keep pending independent-review records out of automated evaluations.

Read opaque committed hashes only, never the private review packet text/labels.
Unranked discovery and source evidence are retained without fit judgments.
"""
import hashlib
import json
import re
from pathlib import Path


def digest(value):return hashlib.sha256(str(value).encode()).hexdigest()


def employer_title_key(row):
    def norm(value):return re.sub(r'[^a-z0-9]+',' ',re.sub(r'\([^)]*\)','',str(value or '')).lower()).strip()
    return norm(row.get('company'))+'|'+norm(row.get('title'))


class ReviewQuarantine:
    def __init__(self,root=None):
        self.urls=set();self.pairs=set()
        root=Path(root) if root else Path(__file__).resolve().parents[1]/'review_holdout'
        for path in root.glob('*/*seal.json'):
            data=json.loads(path.read_text())
            if data.get('status')!='sealed_awaiting_independent_review':continue
            for row in data.get('records',[]):
                for field,target in [('url_sha256',self.urls),('employer_title_sha256',self.pairs)]:
                    value=row.get(field,'')
                    if value and not re.fullmatch(r'[0-9a-f]{64}',value):raise ValueError('Invalid sealed-review commitment')
                    if value:target.add(value)

    def matches(self,row):
        if digest(employer_title_key(row)) in self.pairs:return True
        urls=[row.get('url',''),row.get('original_posting_url','')]
        if isinstance(row.get('employer_board_urls'),list):urls+=row['employer_board_urls']
        for value in urls:
            if not value:continue
            value=str(value).rstrip('/')
            variants={value,value.replace('https://boards.greenhouse.io/','https://job-boards.greenhouse.io/'),
                      value.replace('https://job-boards.greenhouse.io/','https://boards.greenhouse.io/')}
            if any(digest(u) in self.urls for u in variants):return True
        return False


def partition_for_review(records,quarantine=None):
    q=quarantine or ReviewQuarantine();available=[];withheld=[]
    for row in records:(withheld if q.matches(row) else available).append(row)
    return available,withheld
