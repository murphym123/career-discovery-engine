"""Robots rules for the controlled pilot (RFC 9309 section 2.2)."""
import re
from urllib.parse import quote, urlsplit


UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
ESCAPE = re.compile(r"%([0-9a-fA-F]{2})")


def normalize(value):
    def normalize_escape(match):
        char = chr(int(match[1], 16))
        return char if char in UNRESERVED else match[0].upper()

    return ESCAPE.sub(normalize_escape, quote(value, safe="/!$&'()*+,;=:@?%[]"))


def matches(pattern, target):
    """Match from the start with a bounded sequence of literal searches."""
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    head, *parts = pattern.split("*")
    if not parts:
        return target == head if anchored else target.startswith(head)
    if not target.startswith(head):
        return False
    end = len(target)
    if anchored:
        tail = parts.pop()
        if not target.endswith(tail):
            return False
        end -= len(tail)
    start = len(head)
    for part in parts:
        if part:
            index = target.find(part, start, end)
            if index < 0:
                return False
            start = index + len(part)
    return start <= end


class RobotsRules:
    def __init__(self, body, user_agent):
        product = user_agent.split("/", 1)[0].lower()
        groups = []
        agents, rules, pacing = set(), [], []
        has_rules = False
        for line in body.removeprefix("\ufeff").splitlines():
            directive, sep, value = line.split("#", 1)[0].partition(":")
            if not sep:
                continue
            key, value = directive.strip().lower(), value.strip()
            if key == "user-agent":
                if not re.fullmatch(r"[a-zA-Z0-9_-]+|\*", value):
                    continue
                if has_rules:
                    groups.append((agents, rules, pacing))
                    agents, rules, pacing = set(), [], []
                    has_rules = False
                agents.add(value.lower())
            elif agents and key in ("allow", "disallow"):
                has_rules = True
                if value.startswith("/") and not any(c.isspace() or ord(c) < 32 for c in value):
                    rules.append((normalize(value), key == "allow", value))
            elif agents and key in ("crawl-delay", "request-rate"):
                # A subsequent agent starts a new group even if pacing is
                # the only applicable policy in this one.
                has_rules = True
                pacing.append((key, value))
        groups.append((agents, rules, pacing))
        selected = product if any(product in agents for agents, _, _ in groups) else "*"
        self.rules = []
        self.delay = 0
        self.unsupported = None
        for agents, rules, pacing in groups:
            if selected not in agents:
                continue
            self.rules.extend(rules)
            for key, value in pacing:
                rate = re.fullmatch(r"([1-9][0-9]{0,5})/([1-9][0-9]{0,5})", value)
                if key == "request-rate" and rate:
                    requests, seconds = map(int, rate.groups())
                    self.delay = max(self.delay, seconds / requests)
                elif key == "crawl-delay" and re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,6})?", value):
                    self.delay = max(self.delay, float(value))
                else:
                    self.unsupported = f"{key}: {value}"

    def decision(self, url):
        parsed = urlsplit(url)
        target = normalize((parsed.path or "/") + ("?" + parsed.query if parsed.query else ""))
        if target == "/robots.txt":
            return True, None
        best = max(
            ((len(pattern), allow, raw) for pattern, allow, raw in self.rules if matches(pattern, target)),
            default=None,
        )
        if best is None:
            return True, None
        _, allow, raw = best
        return allow, f"{'allow' if allow else 'disallow'}: {raw}"
