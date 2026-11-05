"""Budgeted evidence inspection, separate from candidate-fit scoring.

Signals form overlapping strata, never exclusion gates. Every index record is
retained. One latest-listing sentinel preserves source health/freshness checks;
other choices are seeded and spread over employers, functions and title forms.
"""
import hashlib
import re
from collections import Counter
from career_engine.discovery import geography

FAMILIES={
    "policy_advocacy":r"policy|legislat|government|affairs|advocacy|organize|campaign",
    "research":r"research|analyst|analysis|fellow|scholar",
    "communications":r"communications|editor|media|writer|press|content",
    "operations_programs":r"operations|program|project|office|administrat|assistant|coordinator|events|membership",
    "development":r"development|fundrais|donor|grant|giving|relationship",
    "consulting":r"consult|advisory|strateg|client",
}


def identity(row):
    return row.get("source","")+":"+str(row.get("source_id") or row.get("url", ""))


def family(row):
    title=row.get("title","").lower()
    matches=[name for name,pattern in FAMILIES.items() if re.search(pattern,title)]
    if matches:return matches[0]
    return "other"


def early_signal(row):
    title=row.get("title","").lower()
    if "provider_early_career" in row.get("experience_hint", "") or "provider_internship" in row.get("experience_hint", ""):
        return True
    # 'Senior Office Administrator' stays in the administrative stratum; this is
    # not a seniority rejection or an assertion about actual required experience.
    strong_senior=bool(re.search(r"\b(director|principal|vice president|chief executive|professor)\b",title))
    return not strong_senior and bool(re.search(r"\b(?:assistants?|associates?|coordinators?|aides?|trainees?|interns?|internships?|fellows?|administrators?|secretary|secretaries|organizers?|representatives?|reporters?|specialists?|officers?)\b",title))


def evidence_tier(row):
    """Soft retrieval order; provider metadata outranks title guesses.

    Mixed early/mid labels remain uncertain. This is not a determination of
    qualifications: an uninspected 'early career' listing may still need a JD,
    technical skill or citizenship the candidate lacks.
    """
    hints=row.get("experience_hint","")
    explicit="provider_early_career" in hints or "provider_internship" in hints
    if explicit and not any(x in hints for x in ("provider_mid_career","provider_advanced")):return 0
    if explicit:return 1
    if "provider_advanced" in hints or re.search(r"\b(director|principal|vice president|chief|professor)\b",row.get("title", "").lower()):return 3
    if early_signal(row) and "provider_mid_career" not in hints:return 1
    return 2


def plan_inspections(rows,budget=20,seed=0,strategy="diverse"):
    if not 0<=budget<=30 or strategy not in ("diverse","prefix"):
        raise ValueError("invalid inspection budget/strategy")
    unique={}
    for row in rows:unique.setdefault(identity(row),row)
    rows=list(unique.values())
    if not budget:return []
    if strategy=="prefix":
        return [{"identity":identity(row),"stratum":"source_prefix"} for row in rows[:budget]]
    selected=[];seen=set();employers=Counter();families=Counter();titles=Counter()
    def hashed(row):return hashlib.sha256((str(seed)+"|"+identity(row)).encode()).hexdigest()
    def geo(row):
        label=geography(row.get("location",""))
        return {"dc_dmv":0,"regional_worksite_unknown":1,"unknown":1,"remote_eligibility_unknown":2,"us_remote":2,"elsewhere":3}[label]
    def choose(pool,count,stratum,diversify=True):
        for _ in range(count):
            available=[row for row in pool if identity(row) not in seen]
            if not available:return
            def key(row):
                employer=row.get("company") or identity(row)
                title=re.sub(r"[^a-z0-9]+"," ",row.get("title", "").lower()).strip()
                return (geo(row),evidence_tier(row) if diversify else 0,employers[employer] if diversify else 0,families[family(row)] if diversify else 0,
                        titles[title] if diversify else 0,hashed(row))
            row=min(available,key=key);name=identity(row)
            selected.append(dict(identity=name,stratum=stratum));seen.add(name)
            employers[row.get("company") or name]+=1;families[family(row)]+=1
            titles[re.sub(r"[^a-z0-9]+"," ",row.get("title", "").lower()).strip()]+=1
    # One freshest regional sentinel; do not spend it on a known foreign job
    # merely because a global board lists that job first.
    best_geo=min((geo(row) for row in rows),default=3)
    sentinel=next((row for row in rows if geo(row)==best_geo),None)
    choose([sentinel] if sentinel else [],1,"latest_regional_listing")
    remaining=min(budget,len(rows))-len(selected)
    exploration=max(1,remaining//5) if remaining>=2 else 0
    early=(remaining-exploration)//2
    diversity=(remaining-exploration-early)*2//3
    novelty=remaining-exploration-early-diversity
    choose([row for row in rows if early_signal(row)],early,"plausible_early_career")
    choose(rows,diversity,"employer_function_diversity")
    choose(rows,novelty,"title_exploration")
    # Explicit weak-signal/unknown review stratum, seeded and independent of fit
    # scores; no hard seniority/title exclusion and no candidate-fit reranking.
    pool=[row for row in rows if not early_signal(row)]
    choose(pool,exploration,"low_signal_random_review",diversify=False)
    choose(rows,budget-len(selected),"budget_fill")
    return selected
