"""
Insights Puller
================
Runs once/day (a separate scheduled workflow from the posting cycle) and
refreshes REAL engagement numbers — reach, likes, comments, shares,
saved/views, total_interactions — for every Instagram post logged in
data/performance.csv. This is what turns "daily analysis" from a promise
into something with actual numbers behind it: analytics.best_categories()
uses these to rank which categories/formats are actually working.

Facebook Page post insights aren't pulled here (that needs a different,
FB-specific metric set and permission surface) — Instagram is the primary
growth channel per the growth plan, so it's the one that matters most.
"""

import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from config import settings

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "performance.csv")
BASE = f"https://graph.facebook.com/{settings.GRAPH_VERSION}"

METRIC_FIELDS = ["reach", "likes", "comments", "shares", "saved", "views",
                  "total_interactions"]
PHOTO_METRICS = "reach,saved,likes,comments,shares,total_interactions"
REEL_METRICS = "reach,likes,comments,shares,views,total_interactions"


def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=30) as r:
        return json.load(r)


def fetch_media_insights(media_id, is_reel):
    metrics = REEL_METRICS if is_reel else PHOTO_METRICS
    try:
        res = _get(f"{BASE}/{media_id}/insights",
                    {"metric": metrics, "access_token": settings.META_PAGE_ACCESS_TOKEN})
    except urllib.error.HTTPError as e:
        print(f"insights failed for {media_id}: HTTP {e.code} {e.read().decode(errors='replace')}")
        return {}
    except Exception as e:
        print(f"insights failed for {media_id}: {e}")
        return {}
    return {item["name"]: (item.get("values") or [{}])[0].get("value", 0)
            for item in res.get("data", [])}


def refresh():
    if not os.path.exists(CSV_PATH):
        print("no performance.csv yet — nothing to refresh")
        return
    with open(CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("performance.csv is empty")
        return

    fieldnames = list(rows[0].keys())
    for mf in METRIC_FIELDS:
        if mf not in fieldnames:
            fieldnames.append(mf)

    updated = 0
    for row in rows:
        ig_id = row.get("ig_id")
        if not ig_id:
            continue
        is_reel = str(row.get("is_reel")).lower() == "true"
        data = fetch_media_insights(ig_id, is_reel)
        if not data:
            continue
        for mf in METRIC_FIELDS:
            if mf in data:
                row[mf] = data[mf]
        updated += 1
        print(f"updated insights for {ig_id}: {data}")

    with open(CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"performance.csv refreshed ({updated}/{len(rows)} posts had insights)")


if __name__ == "__main__":
    refresh()
