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
import html
import json
import os
import csv
import urllib.request

from config import settings

POSTED_LOG = os.path.join(os.path.dirname(__file__), "..", "data", "posted.json")
PERFORMANCE_LOG = os.path.join(os.path.dirname(__file__), "..", "data", "performance.csv")


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


# A fresh incident (collapse, crash, disaster) almost never has a real,
# specific photo available yet through any legitimately-licensed source —
# forcing a generic stock substitute into that slot is exactly what's been
# producing mismatches. These stories get flagged so the renderer can use
# a bold graphic alert card instead of guessing at a photo.
INCIDENT_KEYWORDS = [
    "collapse", "collapses", "collapsed", "crash", "crashes", "crashed",
    "accident", "derail", "derails", "derailed", "earthquake", "landslide",
    "flood", "floods", "flooding", "cyclone", "fire", "blast", "explosion",
    "explosions", "rescue", "rescued", "trapped", "stampede", "capsize",
    "capsized", "drown", "drowned", "gas leak", "building collapse",
    "wall collapse", "bridge collapse", "avalanche", "tsunami", "wildfire",
    "hooch", "spurious liquor", "toxic liquor", "poisoning", "stabbed",
    "stabbing", "shooting", "shot dead", "lynched", "lynching",
]


def _any_kw(keywords, text):
    """Word-boundary keyword match. Plain substring `in` checks (the
    original implementation) silently match keywords inside unrelated
    words -- e.g. "row" (a controversy signal) matching inside "Narrows",
    which is how a routine stock-price update ended up outscoring stories
    people would actually stop to share. \\b requires the keyword to be a
    standalone token, not a fragment of a longer word."""
    return any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in keywords)


def is_fresh_incident(title):
    return _any_kw(INCIDENT_KEYWORDS, title.lower())


# ---------- virality scoring ----------
HOT_KEYWORDS = [
    "breaking", "dies", "death", "wins", "record", "attack", "arrest",
    "verdict", "resign", "ban", "crash", "rescue", "historic", "first",
    "biggest", "cr", "crore", "supreme court", "election", "results",
    "cyclone", "earthquake", "budget", "rbi", "isro", "won", "final",
    # curiosity / shareability signals — these are what actually make
    # someone stop scrolling and hit follow, not just "news happened".
    # "row"/"slams"/"backlash"/"controversy" used to be here and were
    # removed: measured against a week of actual selections, they were
    # firing almost entirely on party-vs-party political sniping ("AIADMK
    # Slams Tamil Nadu CM Over Secretariat Plans") rather than on anything
    # that actually spreads. Those headlines cleared the score bar on
    # keyword presence alone and crowded out real incidents -- see
    # POLITICAL_REACTION_KEYWORDS below, which penalises that pattern
    # instead of rewarding it.
    "viral", "shocking", "netizens",
    "exclusive", "leaked", "outrage", "stuns", "stunned",
    "unprecedented", "never before", "world's first", "warns", "alert",
    "scam", "fraud", "explosive", "sensational", "massive", "huge",
]

# Added for the 2026-10-21 niche pivot (option 1 of 3 chosen: lean into
# Tamil cinema specifically for pan-India reach). Tamil Nadu politics and
# state news are, by nature, regional -- but Tamil cinema stars
# (Rajinikanth, Kamal Haasan, Ajith Kumar, Vikram, Suriya, Dhanush...)
# have fan followings across India and internationally (Bengaluru,
# Mumbai, the Tamil diaspora in Singapore/Malaysia/the Gulf/Sri Lanka),
# making this the one TN-adjacent content type that is genuinely NOT
# geographically limited. Deliberately does NOT include "Vijay" alone --
# that name is currently ALSO Tamil Nadu's sitting CM, and a bare-name
# boost would just re-score ordinary state politics as if it were
# cinema content, defeating the point of a targeted boost.
TAMIL_CINEMA_KEYWORDS = [
    "kollywood", "tamil cinema", "tamil film", "tamil movie", "tamil actor",
    "rajinikanth", "kamal haasan", "ajith kumar", "vikram", "suriya",
    "dhanush", "vijay sethupathi", "nayanthara", "trisha", "simbu",
    "silambarasan", "yogi babu", "box office", "kollywood star",
]


def _is_tamil_cinema(title):
    return _any_kw(TAMIL_CINEMA_KEYWORDS, title.lower())


# Added 2026-10-22: a direct follow-up to the Tamil cinema boost above --
# asked for by name ("Bollywood") as a second pan-India reach lever
# within the same INDIAN CINEMA category, not a new state/category of
# its own. Hindi cinema has, if anything, a WIDER unprompted national
# reach than Tamil cinema (it is the one entertainment category genuinely
# followed the same way in every state), which is exactly why it belongs
# here rather than being treated as regional. Full names only, for the
# same reason "Vijay" was excluded from the Tamil list: a bare common
# first name would false-positive on unrelated stories.
BOLLYWOOD_KEYWORDS = [
    "bollywood", "hindi cinema", "hindi film", "shah rukh khan", "srk",
    "salman khan", "aamir khan", "akshay kumar", "ranbir kapoor",
    "deepika padukone", "alia bhatt", "ranveer singh", "katrina kaif",
    "hrithik roshan", "priyanka chopra", "kareena kapoor",
    "amitabh bachchan", "vicky kaushal", "kiara advani",
]


def _is_bollywood(title):
    return _any_kw(BOLLYWOOD_KEYWORDS, title.lower())


# Answers a real, sharp observation (2026-10-22): entertainment is the one
# news category where "real video" and "a text card" are an especially
# wide gap -- a trailer or song release genuinely IS a video, so a
# headline card about one is a flat substitute for footage the studio
# itself just put out specifically to be shared and discussed. But the
# fix is NOT auto-fetching that video: a studio's own official trailer is
# lower-risk than a random bystander clip (distribution is the whole
# point of releasing one), but "lower-risk" still is not "no risk", and
# an automated YouTube search+download would just be scraping at a
# different layer, bypassing the one judgment call that actually matters
# here -- confirming it's really the studio's own channel, not a reupload.
# So this only DETECTS the moment worth flagging and tells the owner,
# via the existing candidate card; sourcing the real link stays a human
# decision, same as every other video this pipeline ever posts. Measured
# on the live INDIAN CINEMA feed: ~1 in 11 headlines are this type.
TRAILER_DROP_KEYWORDS = [
    "trailer", "teaser", "first look", "song release", "motion poster",
    "first single", "lyrical video",
]


def is_trailer_drop(title):
    return _any_kw(TRAILER_DROP_KEYWORDS, title.lower())


def search_trailer_candidates(title, max_results=3):
    """Looks up possible official video matches for a trailer/teaser/song
    headline -- title, channel name and link only, NEVER downloaded here.
    This exists so the owner doesn't have to search YouTube by hand: the
    bot does the lookup, but a human still has to look at the channel
    name and decide it's really the studio's own account (not a reupload)
    before pasting that link back to post it. That one decision is never
    made here -- see is_trailer_drop's docstring for why skipping it would
    just be scraping at a different layer. Any failure (network, no
    yt-dlp, YouTube blocking the search) returns an empty list rather
    than raising, so a flaky search can never freeze candidate posting."""
    try:
        import yt_dlp
    except ImportError:
        return []
    query = title if "trailer" in title.lower() or "teaser" in title.lower() else f"{title} official"
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "extract_flat": "in_playlist",
        "socket_timeout": 10,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"ytsearch{max_results}:{query}", download=False)
    except Exception as e:
        print("search_trailer_candidates failed:", str(e)[:160])
        return []
    results = []
    for e in (info or {}).get("entries") or []:
        vid = e.get("id") or e.get("url")
        if not vid:
            continue
        url = vid if str(vid).startswith("http") else f"https://www.youtube.com/watch?v={vid}"
        results.append({
            "title": (e.get("title") or "").strip(),
            "channel": e.get("channel") or e.get("uploader") or "unknown channel",
            "url": url,
        })
    return results


# ---------- cross-platform trending (Google Trends + YouTube) ----------
# Added 2026-10-23, explicit request: cross-check this pipeline's own
# hot_hit/score signal against what is ACTUALLY trending right now on
# Google and YouTube, not just internal keyword heuristics. A match adds
# a strong score boost -- NOT a hard requirement to post, by explicit
# choice, so a genuinely early/breaking story that hasn't shown up on
# Trends/YouTube YET can still post on its own merits ("catch it before
# it trends" was the actual request).
#
# Instagram has no free, ToS-safe way to check "what's trending" at all
# (no public API for it, and scraping it would be the same kind of
# fragile/risky move this project has avoided before) -- skipped
# entirely, by explicit decision, not an oversight.
GOOGLE_TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=IN"


def google_trending_terms():
    """Today's real-time trending search terms in India, straight from
    Google's own RSS feed -- no API key, no scraping, and deliberately
    NOT pytrends (its old dailytrends endpoint now 404s; Google replaced
    it with this RSS feed, confirmed live 2026-10-23). Returns a
    lowercased set, or an empty set on any failure (network, parsing,
    Google changing the feed again) -- this is an optional signal, never
    a blocker, so a failure here must never stop a story from scoring or
    posting on its own merits."""
    try:
        feed = feedparser.parse(GOOGLE_TRENDS_RSS_URL)
        return {e.title.strip().lower() for e in feed.entries if e.get("title")}
    except Exception as e:
        print("google_trending_terms failed:", str(e)[:160])
        return set()


def youtube_trending_terms(region="IN"):
    """Today's trending YouTube video titles for the given region, via
    the official YouTube Data API v3. YouTube's own public Trending PAGE
    now requires a logged-in session (confirmed 2026-10-23 -- the old
    keyless yt-dlp scrape this project already uses elsewhere no longer
    works against it), so the API is the only reliable free path left.
    Needs settings.YOUTUBE_API_KEY; returns an empty set (not an error)
    when that isn't configured yet, so this stays a harmless no-op until
    the owner adds the key -- same pattern as every other optional
    signal in this module."""
    if not settings.YOUTUBE_API_KEY:
        return set()
    url = (
        "https://www.googleapis.com/youtube/v3/videos"
        f"?part=snippet&chart=mostPopular&regionCode={region}&maxResults=30"
        f"&key={settings.YOUTUBE_API_KEY}"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.load(r)
        return {item["snippet"]["title"].strip().lower()
                for item in data.get("items", [])
                if item.get("snippet", {}).get("title")}
    except Exception as e:
        print("youtube_trending_terms failed:", str(e)[:160])
        return set()


# Cached for the lifetime of one process (one workflow run) -- this
# pipeline fetches up to 3 categories per cycle, each scoring dozens of
# headlines, and nothing here needs (or should make) a fresh network
# call per headline.
_TRENDING_CACHE = {"terms": None, "fetched_at": None}
TRENDING_CACHE_TTL_MIN = 30


def _cross_platform_trending_terms():
    now = dt.datetime.now(dt.timezone.utc)
    fetched_at = _TRENDING_CACHE["fetched_at"]
    if _TRENDING_CACHE["terms"] is not None and fetched_at and \
            (now - fetched_at).total_seconds() < TRENDING_CACHE_TTL_MIN * 60:
        return _TRENDING_CACHE["terms"]
    terms = google_trending_terms() | youtube_trending_terms()
    _TRENDING_CACHE["terms"] = terms
    _TRENDING_CACHE["fetched_at"] = now
    return terms


def _trending_term_bigrams(term):
    """A trending search query is often an awkward multi-word phrase
    (e.g. "samantha ruth prabhu on vijay") that will almost never appear
    verbatim in a written headline, while matching on single loose words
    ("on", "vijay") would false-positive on nearly everything. A
    consecutive word-pair ("samantha ruth") is specific enough to be a
    real signal either way, so phrases are decomposed into bigrams; a
    genuinely single-word trending term is kept whole only if it's long
    enough not to be noise."""
    words = term.lower().split()
    if len(words) == 1:
        return {words[0]} if len(words[0]) >= 4 else set()
    return {f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)}


def is_cross_platform_trending(title, terms=None):
    """Whether this headline overlaps a currently-trending Google/YouTube
    search term (see _trending_term_bigrams for the matching rule).
    `terms` lets callers (and tests) pass a fixed set instead of
    triggering the live, cached lookup."""
    if terms is None:
        terms = _cross_platform_trending_terms()
    t = title.lower()
    return any(frag in t for term in terms for frag in _trending_term_bigrams(term))


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
    return _any_kw(JUNK_KEYWORDS, title.lower())


# Routine, low-engagement financial-wire content (a stock's daily +/-X%
# move, quarterly earnings numbers) gets picked up by several market-data
# feeds simply because they all mirror the same figures off an exchange
# filing -- that corroboration means "widely syndicated," not "people will
# comment and share this." Left unpenalized, this class of story can
# out-rank genuinely engaging news on recency + corroboration alone, which
# is exactly what happened with a Shiprocket quarterly-earnings headline
# scoring 86 and beating actually shareable stories into the WhatsApp queue.
ROUTINE_MARKET_KEYWORDS = [
    "share price", "stock price", "shares rise", "shares fall", "shares gain",
    "shares slip", "shares jump", "shares surge", "shares drop", "shares climb",
    "q1 results", "q2 results", "q3 results", "q4 results", "quarterly results",
    "net profit", "net loss", "target price", "rating upgrade", "rating downgrade",
    "brokerage", "bse", "nse", "sensex", "nifty", "m-cap", "market cap",
]


def _is_routine_market_update(title):
    return _any_kw(ROUTINE_MARKET_KEYWORDS, title.lower())


# Party-vs-party political point-scoring: one leader/party reacting to
# another, with no actual event underneath it. Measured against a live
# run of this pipeline's own selections, headlines matching this pattern
# ("AIADMK Slams Tamil Nadu CM Over Secretariat Plans", "Narendra's
# Ongoing Trump Appeasement: Congress's swipe") made up the majority of a
# day's queue -- they clear the score bar on "attack"/"slams"-style
# keyword presence and multi-outlet corroboration (every outlet quotes
# the same press statement) without any of the actual shareability that
# corroboration is meant to signal. This is a genuinely different content
# type from a real incident, and is scored as one.
POLITICAL_REACTION_KEYWORDS = [
    "slams", "hits back", "swipe at", "takes a dig", "war of words",
    "trades barbs", "counters", "reacts to criticism", "responds to",
    "backlash", "row over", "controversy over", "row erupts",
    "appeasement", "targets", "lashes out",
]


def _is_political_reaction(title):
    return _any_kw(POLITICAL_REACTION_KEYWORDS, title.lower())


# A court/legislative PROCESS update -- a hearing adjourned, a status
# report sought, a notice issued -- is not the underlying story; it is
# procedural noise about a story, usually with no new information and no
# resolution. These were scoring well purely from NATIONAL_IMPACT_KEYWORDS
# ("high court", "cbi") plus recency, the same failure mode as the routine
# market-update case above.
ROUTINE_PROCEDURAL_KEYWORDS = [
    "seeks status report", "status report sought", "posts matter for",
    "adjourns hearing", "hearing adjourned", "issues notice",
    "next hearing", "case listed", "matter listed", "matter posted",
    "seeks response", "seeks reply", "notice issued to",
]


def _is_routine_procedural(title):
    return _any_kw(ROUTINE_PROCEDURAL_KEYWORDS, title.lower())


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


def _virality(title, published_dt, category=None):
    score = 0
    hot_hit = False
    t = title.lower()
    for kw in HOT_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", t):
            score += 12
            hot_hit = True
    # pan-India impact outranks regional/local stories, even other hot ones
    for kw in NATIONAL_IMPACT_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", t):
            score += 25
            hot_hit = True
            break
    # 2026-10-21 niche pivot: Tamil cinema is the one TN-adjacent content
    # type with genuinely pan-India/diaspora reach (see TAMIL_CINEMA_KEYWORDS'
    # comment), so it gets the same treatment as a national-impact story
    # rather than being scored as merely regional content.
    if _is_tamil_cinema(title):
        score += 25
        hot_hit = True
    # 2026-10-22: Bollywood is the second pan-India reach lever, same
    # category treatment as Tamil cinema above (see BOLLYWOOD_KEYWORDS).
    if _is_bollywood(title):
        score += 25
        hot_hit = True
    # 2026-10-23, explicit request: a real, external confirmation that
    # this specific headline is trending right now on Google/YouTube is
    # a stronger signal than any of the keyword heuristics above -- it's
    # not guessing at what spreads, it's observing that it already is.
    if is_cross_platform_trending(title):
        score += 20
        hot_hit = True
    # 2026-10-23, explicit preference order: Tamil Nadu 1st, national
    # India 2nd, cinema 3rd -- weighted, not a strict fill order, so a
    # strong story in any of the three can still win the queue over a
    # weak one in a "higher-ranked" category (see settings.CATEGORY_
    # PRIORITY_BOOST's own comment). This only nudges ranking among
    # stories that already qualify -- top_candidates()'s TRENDING_ONLY
    # gate elsewhere decides whether a story is eligible at all.
    if category:
        score += settings.CATEGORY_PRIORITY_BOOST.get(category, 0)
    # Fresh incidents (collapse/crash/disaster) were the single biggest
    # reach outlier across every competitor account analyzed -- a building
    # collapse and an animal-cruelty clip each cleared 2M+ views while
    # conventional wire-style reporting topped out two orders of magnitude
    # lower. Many INCIDENT_KEYWORDS don't overlap HOT_KEYWORDS (e.g.
    # "collapse", "blast", "trapped"), so without this they weren't
    # contributing to score at all beyond whatever happened to overlap.
    if is_fresh_incident(title):
        score += 20
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
    # a routine stock move / earnings number is low-engagement even when
    # multiple market-wire feeds all cover it -- see ROUTINE_MARKET_KEYWORDS
    if _is_routine_market_update(title):
        score -= 35
    # party-vs-party political point-scoring with no event underneath it
    # -- see POLITICAL_REACTION_KEYWORDS. Strong enough to override the
    # NATIONAL_IMPACT_KEYWORDS bonus above, since "High Court" or "CBI"
    # appearing in a reaction headline doesn't make the reaction itself
    # newsworthy.
    if _is_political_reaction(title):
        score -= 40
        hot_hit = False
    # a procedural court/legislative update (hearing adjourned, notice
    # issued) -- see ROUTINE_PROCEDURAL_KEYWORDS.
    if _is_routine_procedural(title):
        score -= 35
        hot_hit = False
    return score, hot_hit


# ---------- user-submitted breaking news (a link the user found themselves) ----------
def fetch_article_metadata(url):
    """Lightweight, dependency-free extraction of a news article's title
    and description from its public URL -- used when the user personally
    sends a breaking-news link + their own photo/video via Telegram
    (src/main.py's _poll_telegram_replies()), rather than going through
    the RSS pipeline. Prefers Open Graph tags (og:title/og:description),
    which virtually every news publisher sets for their own social-share
    previews, falling back to the plain <title> tag if those are missing."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AravindNews24Bot/1.0)"
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        page = r.read().decode("utf-8", errors="replace")

    def _meta(key, attr="property"):
        pattern = (
            rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']'
            rf'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr}=["\']{re.escape(key)}["\']'
        )
        m = re.search(pattern, page, re.I)
        if not m:
            return None
        return html.unescape((m.group(1) or m.group(2)).strip())

    title = _meta("og:title") or _meta("twitter:title", "name")
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", page, re.I)
        title = html.unescape(m.group(1).strip()) if m else url
    summary = _meta("og:description") or _meta("description", "name") or ""
    return {"title": title, "summary": summary}


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
    if category == "TAMIL NADU NEWS":
        # More specific than the old blanket "India" -- this only affects
        # the Telegram preview line (main.py's loc_line); the actual
        # published post's location tag still always says "India" for
        # any is_india=True story (see publisher.py), unaffected by this.
        return {"is_india": True, "place": "Tamil Nadu"}
    if category == "INDIAN CINEMA":
        return {"is_india": True, "place": "India"}
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
            score, hot_hit = _virality(title, pub, category=category)
            out.append({
                "id": sid,
                "title": title,
                "summary": re.sub("<[^<]+?>", "", e.get("summary", ""))[:400],
                "link": e.get("link", ""),
                "published": pub,
                "category": category,
                "score": score,
                "hot_hit": hot_hit,
                "is_incident": is_fresh_incident(title),
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


def allowed_categories(now_ist=None):
    """Which RSS categories may be posted right now. By request, India's
    waking hours (settings.INDIA_ONLY_*) are India-news only -- world,
    sports and business are held for overnight, when the Indian audience
    is asleep anyway. Returns every category outside that window."""
    if now_ist is None:
        now_ist = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
    hour = now_ist.hour
    start, end = settings.INDIA_ONLY_START_HOUR_IST, settings.INDIA_ONLY_END_HOUR_IST
    india_only = start <= hour < end if start < end else (hour >= start or hour < end)
    if india_only:
        return [c for c in settings.RSS_FEEDS if c in settings.INDIA_ONLY_CATEGORIES]
    return list(settings.RSS_FEEDS)


def top_candidates(n, exclude_ids=frozenset()):
    """Top N distinct stories across every category, for the Telegram
    queue — unlike pick_top_story (one category with fallbacks), this
    scans everything so a handful of genuinely different stories can be
    sent at once. `exclude_ids` keeps it from re-suggesting something
    already sitting in the queue from an earlier cycle.

    TRENDING-ONLY gate (2026-10-23, explicit request): a story must have
    hot_hit=True (the scorer's own flag for a real virality/national-
    impact/incident/cinema-boost signal, not just recency/length filler
    points) AND clear TRENDING_ONLY_MIN_SCORE. Before this, a quiet news
    cycle still filled the queue with whatever scored highest among that
    cycle's candidates, even an otherwise un-notable headline -- there
    was no floor, only a ranking. Returning fewer than `n` (even zero) is
    the correct outcome on a quiet cycle now, not a bug to work around."""
    seen, out = set(), []
    all_candidates = []
    for category in allowed_categories():
        all_candidates.extend(fetch_candidates(category))
    for story in sorted(all_candidates, key=lambda s: s["score"], reverse=True):
        if story["id"] in exclude_ids or story["id"] in seen:
            continue
        if not story.get("hot_hit") or story["score"] < settings.TRENDING_ONLY_MIN_SCORE:
            continue
        seen.add(story["id"])
        story["geo"] = detect_geo(story["title"], story["category"])
        out.append(story)
        if len(out) >= n:
            break
    return out


def already_covered(story):
    """Has this exact story -- or the same real event under different
    wording -- already gone out? fetch_candidates() screens for this when
    a story is first picked up, but a candidate can sit in the review
    queue for a long time afterwards, during which the same event gets
    posted (or re-reported by another outlet with fresh wording). Callers
    use this as a final check immediately before publishing."""
    posted = _load_posted()
    if any(e.get("id") == story.get("id") for e in posted):
        return True
    return _is_recent_duplicate(story.get("title", ""), posted)


def mark_posted(story):
    posted = _load_posted()
    posted.append({"id": story["id"], "title": story["title"]})
    _save_posted(posted)


# ---------- breaking-news fast path ----------
BREAKING_SCORE_THRESHOLD = 90


# A story that's genuinely fresh (published very recently) and hits a
# strong urgency signal is worth breaking on immediately, WITHOUT waiting
# for the corroboration bonus that _apply_corroboration() adds once 2+
# outlets cover the same event -- corroboration is an inherently lagging
# signal (other outlets take time to publish too), so requiring it before
# something counts as "breaking" means always being late to the exact
# stories where being first matters most.
BREAKING_FRESH_MAX_AGE_H = 0.5
BREAKING_FRESH_MIN_SCORE = 55  # recency+keyword score alone, before any corroboration bonus


def find_breaking_story():
    """Scan every category for a story clearing a strict breaking-news bar
    — deliberately higher than the normal per-cycle pick, so this only
    fires for something genuinely exceptional, not just today's best-of-6.
    Two ways in: a high total score (BREAKING_SCORE_THRESHOLD, usually
    reached via corroboration from multiple outlets), OR very fresh +
    strong signal on its own (BREAKING_FRESH_*), so a real breaking event
    doesn't have to wait for other outlets to catch up before it counts."""
    best = None
    for category in allowed_categories():
        for story in fetch_candidates(category):
            if not story.get("hot_hit"):
                continue
            age_h = (dt.datetime.now(dt.timezone.utc) - story["published"]).total_seconds() / 3600
            qualifies = (
                story["score"] >= BREAKING_SCORE_THRESHOLD
                or (age_h <= BREAKING_FRESH_MAX_AGE_H and story["score"] >= BREAKING_FRESH_MIN_SCORE)
            )
            if not qualifies:
                continue
            if best is None or story["score"] > best["score"]:
                best = story
    if best:
        best["geo"] = detect_geo(best["title"], best["category"])
    return best


def _hours_since(timestamp_filter=None):
    """Shared helper: hours since the last logged post matching an
    optional filter on the row (e.g. category == BREAKING NEWS). Returns
    a large number if there's no matching history (nothing to rate-limit
    against)."""
    if not os.path.exists(PERFORMANCE_LOG):
        return 999.0
    with open(PERFORMANCE_LOG) as f:
        rows = list(csv.DictReader(f))
    if timestamp_filter:
        rows = [r for r in rows if timestamp_filter(r)]
    if not rows:
        return 999.0
    try:
        last = dt.datetime.strptime(rows[-1]["timestamp_ist"], "%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        return 999.0
    # `last` is naive (parsed from plain "YYYY-MM-DD HH:MM" text), so
    # now_ist must be naive too before subtracting — mixing an aware and
    # a naive datetime raises TypeError, which is exactly what broke every
    # single run of this check until now.
    now_ist = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)).replace(tzinfo=None)
    return (now_ist - last).total_seconds() / 3600


def hours_since_last_post():
    """How long since the last post of ANY kind actually went out."""
    return _hours_since()


def hours_since_last_breaking_post():
    """How long since the last BREAKING NEWS-labeled post specifically --
    kept independent of hours_since_last_post() so the dedicated breaking-
    news fast path isn't starved by the regular hourly queue sharing the
    same clock (exactly what was happening: breaking-news.yml checked
    hours_since_last_post() too, so once the Telegram queue started
    posting roughly hourly, that check almost never cleared and the
    'genuinely exceptional story' fast path silently never fired)."""
    return _hours_since(lambda r: r.get("category") == "BREAKING NEWS")


def posts_in_last_24h():
    """Total posts logged in the trailing 24h, across every path (regular
    queue + breaking). Instagram's Graph API hard-caps content publishing
    at 25 posts/24h -- this is the shared safety valve so decoupling
    breaking-news's own pacing from the regular queue's can't silently
    stack past that limit."""
    if not os.path.exists(PERFORMANCE_LOG):
        return 0
    now_ist = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)
    cutoff = now_ist.replace(tzinfo=None) - dt.timedelta(hours=24)
    count = 0
    with open(PERFORMANCE_LOG) as f:
        for row in csv.DictReader(f):
            try:
                ts = dt.datetime.strptime(row["timestamp_ist"], "%Y-%m-%d %H:%M")
            except (KeyError, ValueError):
                continue
            if ts >= cutoff:
                count += 1
    return count
