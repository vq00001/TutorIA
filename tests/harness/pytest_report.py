"""Deterministic ordering and content-free result summaries for the host harness."""

import json
import os
import random
import time
from pathlib import Path

_results = []
_collection_errors=[]
_started = time.monotonic()


def pytest_collection_modifyitems(items):
    mode = os.environ.get("TEST_ORDER", "normal")
    if mode == "reverse":
        items.reverse()
    elif mode == "random":
        random.Random(int(os.environ["TEST_SEED"])).shuffle(items)


def pytest_runtest_logreport(report):
    if report.when == "call" or report.when in ("setup", "teardown") and (report.failed or report.skipped):
        _results.append(
            {
                "nodeId": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "seconds": round(report.duration, 6),
            }
        )


def pytest_collectreport(report):
    if report.failed:
        _collection_errors.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    outcomes={}
    severity={'passed':0,'skipped':1,'failed':2}
    for row in _results:
        current=outcomes.get(row['nodeId'],'passed')
        outcomes[row['nodeId']]=max((current,row['outcome']),key=severity.__getitem__)
    result = {
        "seed": int(os.environ.get("TEST_SEED", "0")),
        "order": os.environ.get("TEST_ORDER", "normal"),
        "passed": sum(outcome=='passed' for outcome in outcomes.values()),
        "failed": sum(outcome=='failed' for outcome in outcomes.values()),
        "skipped": sum(outcome=='skipped' for outcome in outcomes.values()),
        'collectionErrors':_collection_errors,
        "seconds": round(time.monotonic() - _started, 3),
        "exitCode": int(exitstatus),
        "tests": _results,
    }
    (Path(os.environ.get("TEST_SUMMARY_DIR", "/tmp")) / "tutoria-summary.json").write_text(
        json.dumps(result, sort_keys=True), encoding="utf-8"
    )
