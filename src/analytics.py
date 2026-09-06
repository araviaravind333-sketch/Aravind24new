"""
Analytics
=========
Logs every post to data/performance.csv and (later) pulls IG/FB insights
so the selector can learn which categories + times drive the most reach.
"""

import os
import csv
import json
import datetime as dt

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
CSV_PATH = os.path.join(DATA, "performance.csv")

FIELDS = ["timestamp_ist", "category", "headline", "score", "is_reel",
          "ig_id", "fb_id", "ig_ok", "fb_ok"]


def log_post(story, written, category_label, results, ist, is_reel=False):
    os.makedirs(DATA, exist_ok=True)
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "timestamp_ist": ist.strftime("%Y-%m-%d %H:%M"),
            "category": category_label,
            "headline": written.get("headline", "")[:120],
            "score": story.get("score", 0),
            "is_reel": is_reel,
            "ig_id": (results.get("instagram") or {}).get("id", ""),
            "fb_id": (results.get("facebook") or {}).get("id", ""),
            "ig_ok": "instagram" in results,
            "fb_ok": "facebook" in results,
        })
    print("Logged to performance.csv")


def best_categories(lookback_days=14):
    """Return categories ranked by how often they posted successfully.
    (Extend later to pull real reach via /insights endpoint.)"""
    if not os.path.exists(CSV_PATH):
        return []
    counts = {}
    cutoff = dt.datetime.now() - dt.timedelta(days=lookback_days)
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            try:
                ts = dt.datetime.strptime(row["timestamp_ist"], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if ts < cutoff:
                continue
            counts[row["category"]] = counts.get(row["category"], 0) + 1
    return sorted(counts, key=counts.get, reverse=True)
