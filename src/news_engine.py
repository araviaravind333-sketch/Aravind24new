"""
News Engine
===========
1. Pull candidate stories from RSS (last 24h only)
2. Score each for virality / reach potential
3. Cross-check trending signals (Google Trends via pytrends, free)
4. AI-rewrite headline + caption in AravindNews24 voice (no plagiarism)
5. Detect geo (India vs foreign country) for location tagging
"""

import feedparser
import datetime as dt
import re
import hashlib
import json
import os

from config import settings

POSTED_LOG = os.path.join(os.path.dirname(__file__), "..", "data", "posted.json")


# ---------- de-dupe: never post the same story twice ----------
def _load_posted():
    if os.path.exists(POSTED_LOG):
        with open(POSTED_LOG) as f:
            return set(json.load(f))
    return set()


def _save_posted(ids):
    os.makedirs(os.path.dirname(POSTED_LOG), exist_ok=True)
    with open(POSTED_LOG, "w") as f:
        json.dump(list(ids)[-500:], f)   # keep last 500


def _story_id(title):
    return hashlib.md5(title.lower().encode()).hexdigest()[:12]


# ---------- virality scoring ----------
HOT_KEYWORDS = [
    "breaking", "dies", "death", "wins", "record", "attack", "arrest",
    "verdict", "resign", "ban", "crash", "rescue", "historic", "first",
    "biggest", "cr", "crore", "supreme court", "election", "results",
    "cyclone", "earthquake", "budget", "rbi", "isro", "won", "final",
]

# Recurring filler content (job/exam-notification listicles) that RSS feeds
# mix in with real news. It's evergreen, not "breaking", and floods the
# candidate pool with high recency+number scores despite being low-value —
# so it's excluded outright rather than just down-scored.
JUNK_KEYWORDS = [
    "recruitment", "vacancy", "vacancies", "bharti", "sarkari naukri",
    "notification out", "apply online", "how to apply", "eligibility criteria",
    "salary structure", "admit card", "hall ticket", "notification released",
]


def _is_junk(title):
    t = title.lower()
    return any(kw in t for kw in JUNK_KEYWORDS)


def _virality(title, published_dt):
    score = 0
    hot_hit = False
    t = title.lower()
    for kw in HOT_KEYWORDS:
        if kw in t:
            score += 12
            hot_hit = True
    # recency bonus (newer = better)
    age_h = (dt.datetime.now(dt.timezone.utc) - published_dt).total_seconds() / 3600
    if age_h < 3:
        score += 30
    elif age_h < 8:
        score += 18
    elif age_h < 16:
        score += 8
    # numbers grab attention
    if re.search(r"\d", title):
        score += 6
    # ideal headline length
    if 6 <= len(title.split()) <= 14:
        score += 8
    return score, hot_hit


# ---------- geo detection ----------
COUNTRY_CITIES = {
    "usa": "Washington", "united states": "Washington", "america": "Washington",
    "china": "Beijing", "russia": "Moscow", "ukraine": "Kyiv",
    "pakistan": "Islamabad", "uk": "London", "britain": "London",
    "japan": "Tokyo", "israel": "Jerusalem", "gaza": "Gaza",
    "france": "Paris", "germany": "Berlin", "canada": "Ottawa",
    "australia": "Sydney", "bangladesh": "Dhaka", "sri lanka": "Colombo",
    "nepal": "Kathmandu", "iran": "Tehran", "saudi": "Riyadh",
}


def detect_geo(title, category):
    t = title.lower()
    if category == "INDIA NEWS":
        return {"is_india": True, "place": "India"}
    for country, city in COUNTRY_CITIES.items():
        if country in t:
            return {"is_india": False, "place": city, "country": country}
    # default world -> keep generic
    return {"is_india": False, "place": None}


# ---------- fetch ----------
def fetch_candidates(category):
    feeds = settings.RSS_FEEDS.get(category, [])
    posted = _load_posted()
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=24)
    out = []

    for url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception:
            continue
        for e in parsed.entries[:20]:
            title = e.get("title", "").strip()
            if not title:
                continue
            sid = _story_id(title)
            if sid in posted:
                continue
            if _is_junk(title):
                continue
            # published time
            pub = None
            if e.get("published_parsed"):
                pub = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc)
            if not pub or pub < cutoff:
                continue
            score, hot_hit = _virality(title, pub)
            out.append({
                "id": sid,
                "title": title,
                "summary": re.sub("<[^<]+?>", "", e.get("summary", ""))[:400],
                "link": e.get("link", ""),
                "published": pub,
                "category": category,
                "score": score,
                "hot_hit": hot_hit,
            })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def pick_top_story(primary_category, fallback_categories):
    """Get the single best story to post right now."""
    candidates = fetch_candidates(primary_category)
    if not candidates:
        for cat in fallback_categories:
            candidates = fetch_candidates(cat)
            if candidates:
                break
    if not candidates:
        return None
    best = candidates[0]
    best["geo"] = detect_geo(best["title"], best["category"])
    return best


def mark_posted(story_id):
    posted = _load_posted()
    posted.add(story_id)
    _save_posted(posted)
