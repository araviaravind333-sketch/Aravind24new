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
import datetime as dt
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


FOLLOWERS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "followers.csv")


def fetch_follower_count():
    """Current follower count straight off the IG account object.

    This exists because of a real gap: the whole point of this account
    is followers, and nothing recorded that number anywhere for over a
    month. performance.csv has eighteen columns and not one of them is
    follows, so every "did that change help?" question was being
    answered with views as a stand-in. Views do not track the goal -- a
    post can reach 800 people and win nobody."""
    try:
        res = _get(f"{BASE}/{settings.IG_USER_ID}",
                   {"fields": "followers_count,media_count",
                    "access_token": settings.META_PAGE_ACCESS_TOKEN})
    except urllib.error.HTTPError as e:
        print(f"follower count failed: HTTP {e.code} {e.read().decode(errors='replace')}")
        return None
    except Exception as e:
        print(f"follower count failed: {e}")
        return None
    return {"followers": res.get("followers_count"), "media": res.get("media_count")}


def record_followers(now_ist=None):
    """Appends one dated row per run -- a log, not a gauge. The useful
    question is never "how many followers" but "did that change bend the
    curve", and only history can answer that."""
    snap = fetch_follower_count()
    if not snap or snap.get("followers") is None:
        return None
    now_ist = now_ist or (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30))
    row = {"timestamp_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
           "followers": snap["followers"], "media": snap.get("media") or ""}
    new = not os.path.exists(FOLLOWERS_PATH)
    os.makedirs(os.path.dirname(FOLLOWERS_PATH), exist_ok=True)
    with open(FOLLOWERS_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp_ist", "followers", "media"])
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"followers: {row['followers']} (logged {row['timestamp_ist']})")
    return row


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
        # extrasaction="ignore" + restval="" -- a historical row picking up
        # a stray column (an unescaped comma in a headline, say) used to
        # raise ValueError here and take the whole daily job down with it.
        # Insights are a nice-to-have refresh; one malformed row must never
        # block every other post's numbers from updating.
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"performance.csv refreshed ({updated}/{len(rows)} posts had insights)")


if __name__ == "__main__":
    try:
        # Followers first: it is the number this account actually exists
        # to grow, and it must get logged even if the per-post insights
        # refresh below hits a bad row or a rate limit.
        try:
            record_followers()
        except Exception as e:
            print(f"follower snapshot failed: {e}")
        refresh()
    except Exception as e:
        # This job updates analytics, nothing user-facing -- it must never
        # take down the workflow with an unhandled traceback over a single
        # bad row or a transient API hiccup that per-post handling didn't
        # already absorb.
        import traceback
        traceback.print_exc()
        print(f"insights refresh failed: {e}")
        raise SystemExit(0)
