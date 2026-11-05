"""Reproducible discovery across role families, locations and broad wildcards."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    family: str
    text: str


FAMILIES = {
    "research": ('research OR insights OR analysis', '"research associate" OR "research assistant"'),
    "policy": ('policy OR legislative OR regulatory', '"policy analyst" OR "legislative correspondent"'),
    "government_affairs": ('"government affairs" OR "government relations" OR "public affairs"', '"public policy" associate coordinator'),
    "advocacy": ('advocacy OR coalition OR organizing OR outreach', '"community engagement" OR "coalition coordinator"'),
    "consulting": ('consulting OR strategy OR advisory OR analyst', '"research and insights" OR "strategic communications"'),
    "communications": ('communications OR editorial OR press OR writing', '"communications associate" OR "press assistant"'),
    "operations": ('operations OR events OR program OR administrative', '"program associate" OR "events coordinator"'),
    "development": ('development OR fundraising OR donor OR membership', '"development assistant" OR "donor relations"'),
    "executive_support": ('"executive assistant" OR "special assistant" OR "staff assistant"', '"strategic initiatives" OR "office of the president"'),
    # Deliberately omit role/experience keywords in the wildcard family.
    "exploration": ('jobs OR hiring OR careers', 'vacancy OR opportunity OR recruitment'),
}
LOCATIONS = ('"Washington DC"', '"Arlington VA" OR "Alexandria VA"',
             '"Bethesda MD" OR "Silver Spring MD"', '"United States" remote')


def query_plan(budget=6, offset=0):
    """Round-robin families; ten queries cover every family at any offset.

    Explicit offsets allow exploration without silently increasing paid requests.
    Each ten-query block changes location, then wording. No employer whitelist.
    """
    if not 1 <= budget <= 20 or offset < 0:
        raise ValueError("invalid query budget or offset")
    families = list(FAMILIES)
    result = []
    for index in range(offset, offset + budget):
        family = families[index % len(families)]
        variant = index // len(families)
        terms = FAMILIES[family][(variant // len(LOCATIONS)) % 2]
        location = LOCATIONS[variant % len(LOCATIONS)]
        result.append(Query(family, f'({terms}) ({location}) (jobs OR hiring OR careers)'))
    return result
