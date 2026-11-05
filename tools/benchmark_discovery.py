"""Compare the actual old/new runners offline. NEVER fetches vacancies.

Reproduce from a clone containing the upstream baseline:
  git show f1ce56c5cf4fdebeb490a447cc73e89a04037f2e:career_engine/__main__.py > /tmp/career-baseline.py
  python -m tools.benchmark_discovery --baseline-engine /tmp/career-baseline.py --output docs/benchmark.json

The baseline file is trusted repository Python code, executed only on synthetic
fixtures with HTTP intercepted. No API credentials are read from the environment.
"""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from career_engine import __main__ as current
from career_engine.queries import query_plan
from career_engine.sources import retrieve
from tools.fixture_harness import FIXTURE, FixtureSession, TEST_ENV, load_fixture

BASELINE_SHA = "f1ce56c5cf4fdebeb490a447cc73e89a04037f2e"
RELEVANT_URLS = {f"https://example.org/jobs/{i}" for i in (*range(12),14,16,17,18)}
ATYPICAL_TITLES = {"Democracy Fellow", "Partnerships Navigator"}


def run_case(engine, sources, *, combined=None, env=None):
    session = FixtureSession()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        argv = ["engine", "--sources", sources, "--db",str(root/"jobs.db"), "--output",str(root/"brief.md")]
        if combined is not None:
            path = root / "combined.csv"
            fields = sorted({k for row in combined for k in row})
            with path.open("w", newline="", encoding="utf-8") as f:
                writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(combined)
            argv += ["--import-csv",str(path)]
        with patch.dict(os.environ,env or {},clear=True), patch("sys.argv",argv), patch("requests.Session",return_value=session), redirect_stdout(io.StringIO()) as stdout:
            exit_code=engine.main()
        if exit_code:
            raise AssertionError(stdout.getvalue())
        with (root/"screening_audit.csv").open(newline="",encoding="utf-8") as f:
            audit=list(csv.DictReader(f))
        summary=json.loads(stdout.getvalue())
        captured={row["url"] for row in audit}
        recs=[row for row in audit if row["decision"]=="recommended"]
        return {
            "received":summary["received"], "unique":summary["unique"],
            "recommended":summary["recommended"], "http_requests":len(session.calls),
            "sources_retrieved":len({name for name,_ in session.calls}),
            "fixture_relevant_captured":len(RELEVANT_URLS & captured),
            "fixture_relevant_total":len(RELEVANT_URLS),
            "fixture_relevant_recommended":sum(row["url"] in RELEVANT_URLS for row in recs),
            "recommended_lanes":len({row["lane"] for row in recs}),
            "atypical_in_exploration":len({row["title"] for row in audit if row["title"] in ATYPICAL_TITLES and row["decision"]=="exploration_review"}),
            "washington_state_recommended":sum(row["company"]=="West Coast Lab" for row in recs),
            "expired_recommended":sum(row["company"]=="Expired Example" for row in recs),
            "same_title_dc_requisitions_retained":sum(row["company"]=="Capitol Evidence Lab" and row["title"]=="Research Associate" and row["location"]=="Washington, DC" for row in audit),
        }


def benchmark(baseline_path):
    spec=importlib.util.spec_from_file_location("baseline_engine",baseline_path)
    baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
    combined=[]
    with patch.dict(os.environ,TEST_ENV,clear=True):
        combined.extend(current.remotive(FixtureSession()))
        for source in ("jobicy","muse","reliefweb","usajobs"):
            combined.extend(retrieve(FixtureSession(),source,6))
    return {
        "kind":"SYNTHETIC FIXTURE BENCHMARK — NOT LIVE VACANCIES OR MARKET COVERAGE",
        "baseline_commit":BASELINE_SHA,
        "baseline_engine_sha256":hashlib.sha256(Path(baseline_path).read_bytes()).hexdigest(),
        "fixture_sha256":hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "cases":{
            "old_free_default":run_case(baseline,"remotive"),
            "new_free_no_credentials":run_case(current,"free"),
            "new_all_free_configured":run_case(current,"free",env=TEST_ENV),
            "old_same_normalized_input":run_case(baseline,"csv",combined=combined),
            "new_same_normalized_input":run_case(current,"csv",combined=combined),
        },
        "query_family_coverage":{
            "old_default_six":4,
            "old_two_identical_default_runs":4,
            "new_default_six":len({q.family for q in query_plan(6)}),
            "new_two_rotated_six_query_runs":len({q.family for q in query_plan(6)+query_plan(6,6)}),
            "taxonomy":"Manually map old queries to research, policy, government_affairs, advocacy, consulting, communications, operations, development, executive_support, exploration. Old first six map to research, government_affairs, government_affairs, operations, research, communications.",
        },
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-engine",required=True,type=Path)
    parser.add_argument("--output",default="docs/benchmark.json",type=Path)
    args=parser.parse_args()
    result=benchmark(args.baseline_engine)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
