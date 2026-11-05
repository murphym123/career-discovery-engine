"""Prepare an isolated, unscored human review packet from raw public listings.

Does not import or call the ranker. Original posting text and links are preserved;
no model-generated qualification summaries, decisions or rankings enter a packet.
Write packets under ignored output/, never tests/ or development datasets.
"""
import argparse
import math
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

BACKGROUND = '''Candidate background (user-reported, not independently verified)
BA in political science with political theory and research training. Internship
experience in political/opposition research, public records and campaign strategy;
outreach, campus recruitment, CRM administration and event coordination at a
think tank; teaching, academic research, political writing/editing and policy
analysis; support for educational programs, fellowships and organizational
operations; advocacy/organizing and some AI-policy research/writing.

Target: approximately 0–3 years with reasonable exceptions; primarily DC/nearby
Maryland and Virginia, plus relevant US-remote opportunities. Broad policy,
political, consulting, communications, advocacy, development, administration,
operations, events, executive support and credible adjacent work are in scope.
No salary, ideology, employer-size or unfamiliar-title exclusion is assumed.

Unknown: exact non-overlapping years by function, specialist credentials,
clearances, work authorization, driving/travel availability and professional
portfolio quality. Do not infer these from interest, an internship title or a BA.
'''
LABEL_FIELDS=['review_id','fit_label','mandatory_requirement_evidence','supported_background',
              'important_gaps','information_missing','availability_evidence','confidence']


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def normalized_pair(r):
    def n(v):return re.sub(r'[^a-z0-9]+',' ',re.sub(r'\([^)]*\)','',str(v or '')).lower()).strip()
    return n(r.get('company')),n(r.get('title'))


def novelty_key(r):return str(r.get('url','')).rstrip('/')


def split_records(records,prior,size=40,seed=9):
    oldurls={novelty_key(r) for r in prior};oldpairs={normalized_pair(r) for r in prior}
    oldids={(r.get('source'),r.get('source_id')) for r in prior if r.get('source_id')}
    oldhashes={r.get('original_description_sha256') or r.get('description_sha256') for r in prior}
    oldhashes.discard(None);oldhashes.discard('')
    unseen=[];seen=set();seenpairs=set();excluded=0
    # Sampling only: keep one representative per employer/title to prevent
    # related location variants straddling a future review and diagnostic set.
    priority={'dc_dmv':0,'us_remote':1,'remote_eligibility_unknown':2,'unknown':3,'regional_worksite_unknown':3,'elsewhere':4}
    ordered_input=sorted(records,key=lambda r:(priority.get(r.get('geography','unknown'),3),digest(novelty_key(r))))
    for r in ordered_input:
        key=novelty_key(r)
        bodyhash=digest(str(r.get('description') or '')) if r.get('description') else ''
        if not key or key in seen or normalized_pair(r) in seenpairs or key in oldurls or normalized_pair(r) in oldpairs or (r.get('source'),r.get('source_id')) in oldids or (bodyhash and bodyhash in oldhashes):
            excluded+=1;continue
        seen.add(key);seenpairs.add(normalized_pair(r));unseen.append(r)
    # Geography is observed metadata, not a fit prediction. Quotas favor the
    # target market but preserve ambiguous and off-market controls explicitly.
    buckets=defaultdict(list)
    for r in unseen:buckets[r.get('geography','unknown')].append(r)
    def ordered(rows):
        groups=defaultdict(list)
        for r in rows:groups[(r.get('source','unspecified'),r.get('company',''))].append(r)
        for group in groups.values():group.sort(key=lambda r:digest(f"{seed}|{novelty_key(r)}"))
        out=[]
        while groups:
            for key in sorted(groups):
                out.append(groups[key].pop(0))
                if not groups[key]:del groups[key]
        return out
    chosen=[]
    weights=[('dc_dmv',.65),('us_remote',.10),('remote_eligibility_unknown',.10),
             ('regional_worksite_unknown',.025),('unknown',.05),('elsewhere',.075)]
    for geo,share in weights:chosen+=ordered(buckets[geo])[:min(int(size*share),math.ceil(len(buckets[geo])*.6))]
    used={novelty_key(r) for r in chosen}
    # Fill sparse strata by target geography before ambiguous/off-market records.
    for geo,_ in weights:
        for r in ordered(buckets[geo]):
            if len(chosen)>=size:break
            geo_used=sum(x.get('geography','unknown')==geo for x in chosen)
            if novelty_key(r) not in used and geo_used<math.ceil(len(buckets[geo])*.6):chosen.append(r);used.add(novelty_key(r))
    chosen=sorted(chosen,key=lambda r:digest(f"blind-order|{seed}|{novelty_key(r)}"))
    diagnostic=[r for r in unseen if novelty_key(r) not in used]
    return chosen,diagnostic,{'input_count':len(records),'unseen_count':len(unseen),'excluded_prior_or_duplicate':excluded,
                             'blind_count':len(chosen),'diagnostic_count':len(diagnostic),'seed':seed}


def write_packet(records,output_dir):
    output_dir=Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):raise ValueError('Refusing to overwrite an existing review packet or labels')
    output_dir.mkdir(parents=True,exist_ok=True)
    review=[];commitments=[]
    lines=['# Blinded job review packet','',
           'No engine scores, ranks, decisions, shortlist membership or generated qualification judgments appear here. Order is shuffled independently of the model. Labels remain blank until a reviewer assesses the original evidence.',
           '', '## Candidate background','',BACKGROUND,'## Review instructions','',
           'Read the original posting and distinguish mandatory requirements, preferences, equivalence and learnable skills. Choose plausible, stretch, unlikely, ineligible or unknown. These are review judgments, not hiring outcomes. Use unknown when the description/eligibility is incomplete. Assess fit separately from whether the vacancy is currently open.',
           '', 'Use labels.csv with the opaque review IDs. Leave availability unknown unless independent current evidence supports it. Do not inspect ranker outputs before submitting labels. This packet is quarantined from development and CI test data.','']
    for n,r in enumerate(records,1):
        rid=f'R{n:03d}'
        body=str(r.get('description') or '')
        # Fixed field allowlist: arbitrary input model fields never propagate.
        item={'review_id':rid,'title':r.get('title',''),'employer':r.get('company',''),
              'location':r.get('location',''),'original_posting':r.get('url',''),
              'source':r.get('source',''),'retrieved_at':r.get('retrieved_at',''),
              'posted_at':r.get('posted_at',''),'closing_at':r.get('closing_at',''),
              'original_description':body,'description_truncated':r.get('description_truncated',False),
              'observation':'Public board observed; employer-active availability not independently verified.'}
        review.append(item)
        commitments.append({'review_id':rid,'url_sha256':digest(novelty_key(r)),
                            'employer_title_sha256':digest('|'.join(normalized_pair(r))),
                            'description_sha256':digest(body) if body else ''})
        lines += [f"## {rid} — {item['title']}",'',f"Employer: {item['employer']} · Location as supplied: {item['location'] or 'not established'}",'',
                  f"Original posting: {item['original_posting']}",'',
                  f"Board observation: {item['retrieved_at']} · Posted: {item['posted_at'] or 'not stated'} · Closing: {item['closing_at'] or 'not stated'}",'',
                  item['observation'],'', '### Original description and qualifications','',
                  body or '[No description retrieved. Requirements cannot be established from the title alone.]','',
                  'Description truncated: '+str(item['description_truncated']), '']
    (output_dir/'PACKET.md').write_text('\n'.join(lines),encoding='utf-8')
    (output_dir/'packet.json').write_text(json.dumps({'kind':'BLINDED ORIGINAL-TEXT HUMAN REVIEW; NO MODEL OUTPUTS',
        'background':BACKGROUND,'records':review},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    with (output_dir/'labels.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=LABEL_FIELDS);w.writeheader();w.writerows({'review_id':r['review_id']} for r in review)
    (output_dir/'reviewer-attestation.json').write_text(json.dumps({
        'reviewer_kind':'','independent_of_model_authorship':None,'blinded_to_outputs_until_labels_frozen':None,
        'labels_frozen_at':'','notes':'Complete only after review. Empty fields are not evidence of independent validation.'},indent=2)+'\n')
    packet_files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output_dir.iterdir() if p.is_file()}
    return {'status':'sealed_awaiting_independent_review','independent_review_available':False,
            'model_evaluated_on_packet':False,'labels_present':False,'records':commitments,'packet_file_sha256':packet_files}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',required=True);p.add_argument('--prior',required=True)
    p.add_argument('--output-dir',required=True);p.add_argument('--diagnostic-output',required=True)
    p.add_argument('--seal-output',required=True);p.add_argument('--size',type=int,default=40);p.add_argument('--seed',type=int,default=9)
    args=p.parse_args(argv)
    if not 1<=args.size<=100:p.error('size must be 1–100')
    paths=[Path(args.output_dir),Path(args.diagnostic_output),Path(args.seal_output)]
    if any('tests' in p.parts for p in paths):p.error('Review data must not be written into test fixtures')
    capture=json.loads(Path(args.capture).read_text());prior=json.loads(Path(args.prior).read_text())['records']
    blind,diagnostic,summary=split_records(capture['records'],prior,args.size,args.seed)
    seal=write_packet(blind,args.output_dir)
    seal.update(summary=summary,capture_sha256=hashlib.sha256(Path(args.capture).read_bytes()).hexdigest(),
                prior_sha256=hashlib.sha256(Path(args.prior).read_bytes()).hexdigest())
    Path(args.diagnostic_output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.diagnostic_output).write_text(json.dumps({'kind':'UNSEALED DIAGNOSTIC PARTITION; NOT INDEPENDENT VALIDATION',
        'captured_at':capture['captured_at'],'records':diagnostic},indent=2,ensure_ascii=False)+'\n')
    Path(args.seal_output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.seal_output).write_text(json.dumps(seal,indent=2)+'\n')
    print(json.dumps(summary));return 0


if __name__=='__main__':raise SystemExit(main())
