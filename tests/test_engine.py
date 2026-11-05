import unittest
from career_engine.__main__ import canonical, clean, score, select

class EngineTests(unittest.TestCase):
    def test_tracking_removed(self):
        self.assertEqual(canonical("https://example.org/jobs/7?utm_source=test"), "https://example.org/jobs/7")
    def test_duplicates(self):
        a=clean({"title":"Policy Associate","company":"Test Org","location":"Washington DC","url":"https://example.org/a"})
        b=clean({"title":"Policy Associate","company":"Test Org","location":"Washington DC","url":"https://example.org/b"})
        self.assertEqual(a["key"],b["key"])
    def test_special_assistant(self):
        a=clean({"title":"Special Assistant, Strategic Initiatives","company":"Test Org","location":"Washington DC","url":"https://example.org/a","description":"policy research and event coordination"})
        value,lane=score(a)
        self.assertGreaterEqual(value,35)
    def test_senior_penalty(self):
        a=clean({"title":"Senior Director, Government Affairs","company":"Test Org","location":"Washington DC","url":"https://example.org/a"})
        self.assertLess(score(a)[0],35)
    def test_no_duplicate_titles_in_shortlist(self):
        jobs=[dict(title="Program Coordinator",company="Org "+str(i),score=60-i,lane="operations") for i in range(6)]
        self.assertEqual(len(select(jobs,10)),2)

if __name__ == "__main__":
    unittest.main()
