import unittest

from tools.pilot39 import USER_AGENT
from tools.pilot39_robots import RobotsRules


class PilotRobotsTests(unittest.TestCase):
    def test_common_crawl_catalog_exception_keeps_index_denied(self):
        rules = RobotsRules("""User-agent: *
Disallow: /

Allow: /$
Allow: /collinfo.json$
Allow: /.well-known/*.txt$
""", USER_AGENT)
        for target in ("/", "/collinfo.json", "/.well-known/a.txt"):
            self.assertTrue(rules.decision("https://index.commoncrawl.org" + target)[0])
        for target in ("/collinfo.json?other=1", "/CC-MAIN-2026-44-index?output=json",
                       "/.well-known/a.txt.bak"):
            self.assertEqual(rules.decision("https://index.commoncrawl.org" + target),
                             (False, "disallow: /"))

    def test_repeated_groups_and_extension_records_like_lever(self):
        rules = RobotsRules("""\ufeffUser-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
Allow: /

User-agent: GPTBot
Disallow: /

User-agent: *
Crawl-delay: 1
Disallow: /private/*
Allow: /private/public$
""", USER_AGENT)
        self.assertEqual(rules.delay, 1)
        self.assertTrue(rules.decision("https://jobs.lever.co/example")[0])
        self.assertTrue(rules.decision("https://jobs.lever.co/private/public")[0])
        self.assertFalse(rules.decision("https://jobs.lever.co/private/public?extra=1")[0])

    def test_specific_groups_combine_with_tie_and_query_rules(self):
        rules = RobotsRules("""User-agent: *
Disallow: /
User-agent: CareerDiscoveryResearchBot
Disallow: /apply
Allow: /apply
Disallow: /*?mode=preview
User-agent: careerdiscoveryresearchbot
Sitemap: https://example.com/sitemap.xml
Disallow: /apply/internal
Allow: /apply/internal/listed
""", USER_AGENT)
        for target in ("/", "/apply", "/apply/internal/listed"):
            self.assertTrue(rules.decision("https://example.com" + target)[0])
        for target in ("/apply/internal", "/apply?mode=preview&x=1"):
            self.assertFalse(rules.decision("https://example.com" + target)[0])

    def test_encoding_keeps_escaped_metacharacters_literal(self):
        rules = RobotsRules("""User-agent: *
Disallow: /café
Disallow: /a%2fb
Disallow: /jobs/%2A$
Disallow: /hidden/%7Etest
""", USER_AGENT)
        for target in ("/caf%C3%A9", "/a%2Fb", "/jobs/%2a", "/hidden/~test"):
            self.assertFalse(rules.decision("https://example.com" + target)[0])
        for target in ("/a/b", "/jobs/analyst", "/jobs/%2A?mode=json"):
            self.assertTrue(rules.decision("https://example.com" + target)[0])
