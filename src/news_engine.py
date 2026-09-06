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
    """Each entry is {"id": <hash>, "title": <original title>}. Older log
    entries were plain hash strings (no near-duplicate title to compare
    against) — kept working via the isinstance check below."""
    if os.path.exists(POSTED_LOG):
        with open(POSTED_LOG) as f:
            raw = json.load(f)
        return [{"id": e, "title": None} if isinstance(e, str) else e for e in raw]
    return []


def _save_posted(entries):
    os.makedirs(os.path.dirname(POSTED_LOG), exist_ok=True)
    with open(POSTED_LOG, "w") as f:
        json.dump(entries[-500:], f)   # keep last 500


def _story_id(title):
    return hashlib.md5(title.lower().encode()).hexdigest()[:12]


def _is_recent_duplicate(title, posted_entries):
    """Same real event, re-covered with different wording as it develops
    ("6 rescued" -> "8 rescued, search continues") isn't caught by the
    exact-title hash check — this compares the same signature-word overlap
    used for corroboration, against everything posted recently."""
    sig = _signature_words(title)
    if not sig:
        return False
    for entry in posted_entries:
        old_title = entry.get("title")
        if old_title and len(sig & _signature_words(old_title)) >= 3:
            return True
    return False


# ---------- virality scoring ----------
HOT_KEYWORDS = [
    "breaking", "dies", "death", "wins", "record", "attack", "arrest",
    "verdict", "resign", "ban", "crash", "rescue", "historic", "first",
    "biggest", "cr", "crore", "supreme court", "election", "results",
    "cyclone", "earthquake", "budget", "rbi", "isro", "won", "final",
    # curiosity / shareability signals — these are what actually make
    # someone stop scrolling and hit follow, not just "news happened"
    "viral", "shocking", "netizens", "row", "slams", "backlash",
    "exclusive", "leaked", "outrage", "controversy", "stuns", "stunned",
    "unprecedented", "never before", "world's first", "warns", "alert",
    "scam", "fraud", "explosive", "sensational", "massive", "huge",
]

# Stories tied to a pan-India institution/event affect literally everyone,
# not just one state/city — these get priority over regional news, per
# explicit request, even over other "hot" stories.
NATIONAL_IMPACT_KEYWORDS = [
    "rbi", "supreme court", "parliament", "lok sabha", "rajya sabha",
    "union budget", "prime minister", "pm modi", "union cabinet", "gst",
    "election commission", "isro", "cbi", "income tax", "indian railways",
    "aadhaar", "president of india", "union government", "central government",
    "nationwide", "across india", "all states", "high court", "cji",
    "national security", "army chief", "defence ministry", "home ministry",
]

# Recurring filler content (job/exam-notification listicles, admission/
# counseling process updates) that RSS feeds mix in with real news. It's
# evergreen bureaucratic process content, not "breaking" — and it's also
# nearly impossible to illustrate well (obscure exam acronyms like "ICET"
# collide with unrelated things in stock/photo search — Openverse mostly
# indexes it as a German train model, not the Indian entrance exam). Both
# problems solved by not posting this class of content at all.
JUNK_KEYWORDS = [
    "recruitment", "vacancy", "vacancies", "bharti", "sarkari naukri",
    "notification out", "apply online", "how to apply", "eligibility criteria",
    "salary structure", "admit card", "hall ticket", "notification released",
    "web options", "web counselling", "web counseling", "choice filling",
    "seat allotment", "spot admission", "certificate verification",
    "rank card", "mock allotment", "counselling schedule", "counseling schedule",
    "revise options", "exercise options", "option entry", "allotment result",
]


def _is_junk(title):
    t = title.lower()
    return any(kw in t for kw in JUNK_KEYWORDS)


_SIG_STOPWORDS = {
    "with", "from", "after", "over", "says", "said", "have", "this", "that",
    "will", "their", "into", "amid", "amidst", "against", "under", "than",
    "more", "most", "what", "when", "where", "which", "while", "about",
}


def _signature_words(title):
    words = re.findall(r"[a-z]{4,}", title.lower())
    return set(w for w in words if w not in _SIG_STOPWORDS)


def _apply_corroboration(candidates):
    """Same real-world event covered near-identically by 2+ independent
    RSS sources is a much stronger 'this actually matters' signal than
    recency or keyword hits alone — boost it, and treat heavy coverage
    (3+ sources) as equivalent to a hot-keyword hit for BREAKING labeling."""
    sigs = [_signature_words(c["title"]) for c in candidates]
    for i, c in enumerate(candidates):
        others_covering = sum(
            1 for j, s in enumerate(sigs)
            if j != i and len(sigs[i] & s) >= 3
        )
        if others_covering:
            c["score"] += min(others_covering, 2) * 15
            c["corroborated"] = others_covering + 1
            if others_covering >= 2:
                c["hot_hit"] = True
    return candidates


def _virality(title, published_dt):
    score = 0
    hot_hit = False
    t = title.lower()
    for kw in HOT_KEYWORDS:
        if kw in t:
            score += 12
            hot_hit = True
    # pan-India impact outranks regional/local stories, even other hot ones
    for kw in NATIONAL_IMPACT_KEYWORDS:
        if kw in t:
            score += 25
            hot_hit = True
            break
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
    posted_ids = {e["id"] for e in posted}
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
            if sid in posted_ids:
                continue
            if _is_recent_duplicate(title, posted):
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
    out = _apply_corroboration(out)
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


def mark_posted(story):
    posted = _load_posted()
    posted.append({"id": story["id"], "title": story["title"]})
    _save_posted(posted)
