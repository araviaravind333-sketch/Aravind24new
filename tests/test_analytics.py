"""
Analytics -- Tests
===================
best_categories()/category_avg_reach() had no coverage at all before
this -- they also turned out to be dead code (never called from the
actual selection pipeline) until 2026-11-02, when category_avg_reach()
was wired into news_engine._category_boost() to replace a static,
guessed category preference with one based on the account's own
measured reach. These tests never touch the real data/performance.csv:
analytics.CSV_PATH is pointed at a temp file per test instead.

    python -m tests.test_analytics
"""

import csv
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import analytics

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=analytics.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: "" for k in analytics.FIELDS}, **r})


def _row(category, reach, days_ago=0):
    ts = dt.datetime.now() - dt.timedelta(days=days_ago)
    return {"timestamp_ist": ts.strftime("%Y-%m-%d %H:%M"), "category": category, "reach": reach}


def category_avg_reach_tests():
    print("\nCATEGORY_AVG_REACH")
    real_path = analytics.CSV_PATH
    with tempfile.TemporaryDirectory() as d:
        analytics.CSV_PATH = os.path.join(d, "performance.csv")
        try:
            check("no CSV at all returns an empty dict, not an error",
                  analytics.category_avg_reach() == {})

            _write_csv(analytics.CSV_PATH, [
                _row("TAMIL NADU NEWS", 60), _row("TAMIL NADU NEWS", 70),
                _row("TAMIL NADU NEWS", 65), _row("TAMIL NADU NEWS", 68),
                _row("TAMIL NADU NEWS", 72),
                _row("INDIAN CINEMA", 200), _row("INDIAN CINEMA", 220),
                _row("INDIAN CINEMA", 210), _row("INDIAN CINEMA", 215),
                _row("INDIAN CINEMA", 225),
                _row("INDIA NEWS", 50), _row("INDIA NEWS", 60),   # only 2 -- below min_samples
            ])
            result = analytics.category_avg_reach(min_samples=5)
            check("a category with enough samples gets its real average reach",
                  abs(result["TAMIL NADU NEWS"] - 67.0) < 0.01, result)
            check("a second category with enough samples is independent of the first",
                  abs(result["INDIAN CINEMA"] - 214.0) < 0.01, result)
            check("a category below min_samples is left out entirely, not averaged on too little data",
                  "INDIA NEWS" not in result, result)
            check("the real-world ordering this was built to fix comes through correctly",
                  result["INDIAN CINEMA"] > result["TAMIL NADU NEWS"], result)

            _write_csv(analytics.CSV_PATH, [
                _row("INDIA NEWS", 999, days_ago=30),
                _row("INDIA NEWS", 10, days_ago=1),
                _row("INDIA NEWS", 10, days_ago=1),
                _row("INDIA NEWS", 10, days_ago=1),
                _row("INDIA NEWS", 10, days_ago=1),
                _row("INDIA NEWS", 10, days_ago=1),
            ])
            result = analytics.category_avg_reach(lookback_days=14, min_samples=5)
            check("a post older than the lookback window is excluded from the average",
                  abs(result["INDIA NEWS"] - 10.0) < 0.01, result)

            _write_csv(analytics.CSV_PATH, [
                {"timestamp_ist": "not-a-real-timestamp", "category": "INDIA NEWS", "reach": "50"},
                _row("INDIA NEWS", 50), _row("INDIA NEWS", 55),
                _row("INDIA NEWS", 52), _row("INDIA NEWS", 58), _row("INDIA NEWS", 60),
            ])
            result = analytics.category_avg_reach(min_samples=5)
            check("a row with an unparseable timestamp is skipped, not a crash",
                  "INDIA NEWS" in result, result)
        finally:
            analytics.CSV_PATH = real_path


def best_categories_tests():
    print("\nBEST_CATEGORIES")
    real_path = analytics.CSV_PATH
    with tempfile.TemporaryDirectory() as d:
        analytics.CSV_PATH = os.path.join(d, "performance.csv")
        try:
            check("no CSV at all returns an empty list, not an error",
                  analytics.best_categories() == [])

            _write_csv(analytics.CSV_PATH, [
                _row("TAMIL NADU NEWS", 67), _row("INDIAN CINEMA", 218),
                _row("INDIA NEWS", 0),   # a post with no reach data yet -- shouldn't crash the ranking
            ])
            ranked = analytics.best_categories()
            check("the higher-reach category ranks first",
                  ranked[0] == "INDIAN CINEMA", ranked)
            check("every category seen still appears in the ranking, even with zero reach logged",
                  set(ranked) == {"TAMIL NADU NEWS", "INDIAN CINEMA", "INDIA NEWS"}, ranked)
        finally:
            analytics.CSV_PATH = real_path


if __name__ == "__main__":
    category_avg_reach_tests()
    best_categories_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
