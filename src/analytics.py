"""
Analytics
=========
Logs every post to data/performance.csv. The engagement columns (reach,
likes, etc.) start blank and get filled in later by src/insights.py, which
runs once/day in a separate workflow — IG doesn't have meaningful numbers
to report seconds after publish, so this is deliberately a two-step log,
not a TODO stub.
"""

import os
import csv
import datetime as dt

from src.template import VARIANTS

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
CSV_PATH = os.path.join(DATA, "performance.csv")

# Keep this list in sync with src/insights.py's METRIC_FIELDS — both write
# the same CSV and must agree on columns or rows will misalign.
FIELDS = ["timestamp_ist", "category", "headline", "score", "is_reel", "template",
          "ig_id", "fb_id", "ig_ok", "fb_ok",
          "reach", "likes", "comments", "shares", "saved", "views",
          "total_interactions"]


def log_post(story, written, category_label, results, ist, is_reel=False, template=None):
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
            "template": template or "",
            "ig_id": (results.get("instagram") or {}).get("id", ""),
            "fb_id": (results.get("facebook") or {}).get("id", ""),
            "ig_ok": "instagram" in results,
            "fb_ok": "facebook" in results,
        })
    print("Logged to performance.csv")


def best_categories(lookback_days=14):
    """Rank categories by average reach where real insights data exists
    (filled in daily by src/insights.py); falls back to post-count for
    categories with no insights yet (e.g. brand new account)."""
    if not os.path.exists(CSV_PATH):
        return []
    cutoff = dt.datetime.now() - dt.timedelta(days=lookback_days)
    counts, reach_totals = {}, {}
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            try:
                ts = dt.datetime.strptime(row["timestamp_ist"], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if ts < cutoff:
                continue
            cat = row["category"]
            counts[cat] = counts.get(cat, 0) + 1
            try:
                reach = float(row.get("reach") or 0)
            except ValueError:
                reach = 0
            if reach:
                reach_totals.setdefault(cat, []).append(reach)

    def _score(cat):
        reaches = reach_totals.get(cat)
        avg_reach = sum(reaches) / len(reaches) if reaches else 0
        return (avg_reach, counts.get(cat, 0))

    return sorted(counts, key=_score, reverse=True)


def best_template(category, lookback_days=14, min_samples=3):
    """Rank template variants for a category by average reach. Returns []
    until at least `min_samples` reach-rated posts exist for this category —
    a preference based on 1-2 data points isn't a real signal yet."""
    if not os.path.exists(CSV_PATH):
        return []
    cutoff = dt.datetime.now() - dt.timedelta(days=lookback_days)
    reach_by_template = {}
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            if row.get("category") != category or not row.get("template"):
                continue
            try:
                ts = dt.datetime.strptime(row["timestamp_ist"], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if ts < cutoff:
                continue
            try:
                reach = float(row.get("reach") or 0)
            except ValueError:
                reach = 0
            if reach:
                reach_by_template.setdefault(row["template"], []).append(reach)

    total_samples = sum(len(v) for v in reach_by_template.values())
    if total_samples < min_samples:
        return []
    return sorted(reach_by_template,
                  key=lambda t: sum(reach_by_template[t]) / len(reach_by_template[t]),
                  reverse=True)


def template_counts(category, lookback_days=30):
    """How many times each variant has been used for this category recently
    — used to rotate evenly across templates before there's enough reach
    data to pick a real winner."""
    counts = {v: 0 for v in VARIANTS}
    if not os.path.exists(CSV_PATH):
        return counts
    cutoff = dt.datetime.now() - dt.timedelta(days=lookback_days)
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            if row.get("category") != category:
                continue
            tmpl = row.get("template")
            if tmpl not in counts:
                continue
            try:
                ts = dt.datetime.strptime(row["timestamp_ist"], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if ts < cutoff:
                continue
            counts[tmpl] += 1
    return counts
