"""
Image Source
============
Gets a high-quality, LEGALLY SAFE photo for the story. Order:
  1. Wikimedia Commons — searched for a real named person/place/institution
     found in the headline (e.g. "Revanth Reddy", "RBI"). Everything on
     Commons is explicitly free-licensed (CC/public domain) by its own
     policy, so this is the one source that can show the ACTUAL subject
     of the story without any copyright risk.
  2. Openverse — a free (no API key) aggregator covering Wikimedia Commons
     + Flickr Creative Commons + museum archives. Tried after Commons for
     named subjects Commons's own search doesn't surface well.
  3. Pexels, then Unsplash — real stock photography, keyword-matched to
     the headline, for stories with no specific named subject.
  4. Openverse again — broadest net, last resort, for topical queries
     neither Pexels nor Unsplash matched.
No AI-generated images (never a hallucinated scene), no publisher/wire
photos scraped from news sites or search engines (copyright / account-ban
risk — this is the thing that actually gets pages nuked at scale).
"""

import os
import re
import urllib.request
import urllib.parse
import json

from config import settings

TMP = os.path.join(os.path.dirname(__file__), "..", "data", "tmp_image.jpg")

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
    "is", "are", "was", "were", "with", "by", "from", "as", "after",
    "over", "amid", "amidst", "into", "out", "up", "down", "this", "that",
    "will", "has", "have", "had", "its", "it's", "says", "said", "not",
    "new", "latest",
}


def _save(data, path=TMP):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _keywords(title, limit=4):
    """Pull the most meaningful words out of the headline for a photo search."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", title)
    seen = []
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or len(w) < 3:
            continue
        if lw not in [s.lower() for s in seen]:
            seen.append(w)
        if len(seen) >= limit:
            break
    return seen


_ENTITY_STOPSTART = {
    "the", "a", "an", "this", "that", "why", "how", "what", "who", "when",
}


def _proper_noun_phrases(title, limit=3):
    """Pull real named-entity candidates (people/places/institutions) out of
    the headline — runs of capitalized words, plus short ALL-CAPS acronyms
    (RBI, ISRO, TCS) — most specific (longest) first."""
    pattern = re.compile(
        r"\b[A-Z][a-zA-Z']*(?:\s+(?:of|and|the|de)\s+[A-Z][a-zA-Z']*|\s+[A-Z][a-zA-Z']*)*\b"
    )
    seen, out = set(), []
    for m in pattern.finditer(title):
        c = m.group().strip()
        words = c.split()
        if c.lower() in _ENTITY_STOPSTART:
            continue
        is_multi_word = len(words) >= 2
        is_acronym = c.isupper() and 2 <= len(c) <= 6
        if not (is_multi_word or is_acronym):
            continue
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    out.sort(key=len, reverse=True)
    return out[:limit]


_COMMONS_UA = "AravindNews24Bot/1.0 (https://github.com/araviaravind333-sketch/Aravind24new)"
# Filename-based only — this catches a file literally named "X logo.png",
# but can't detect a small watermark baked into a real photo's pixels (e.g.
# some government press-office handouts stamp a corner logo). That needs
# actual image recognition (paid Vision APIs), which is out of scope for a
# zero-cost pipeline — a rare watermarked press photo can still slip through.
_LOGO_HINTS = (
    "logo", "icon", "wordmark", "emblem", "seal", "coat of arms",
    "banner", "poster", "advertisement", "watermark", "letterhead",
    "press release", "screenshot", "infographic",
)


def _text_relevance(words, text):
    """How many of `words` actually appear in `text` — used to reject a
    result that merely matched the search API's own fuzzy/full-text ranking
    without actually depicting the subject (e.g. a market-street stock photo
    surfacing for "Delhi building collapse" because it's tagged "Delhi")."""
    word_set = {w.lower() for w in words if len(w) >= 3}
    text_words = set(re.findall(r"[a-zA-Z]+", (text or "").lower()))
    return len(word_set & text_words)


def _title_relevance(entity, file_title):
    """How many of the entity's significant words actually appear in the
    file's title. Commons' full-text search matches on categories/
    descriptions too, so a query with little dedicated coverage (e.g. a
    small neighborhood name) can return something merely keyword-adjacent —
    a violinist for a Delhi building collapse story, say. Requiring the
    title itself to actually mention the subject catches that."""
    return _text_relevance(entity.split(), file_title)


def _wikimedia_commons(entity):
    """Search Commons for a real photo of a specific named subject. Every
    file on Commons is required by its own policy to be free-licensed, so
    no separate license check is needed — but logos/icons/svg wordmarks are
    filtered out since those carry separate trademark risk, not copyright."""
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrsearch": f'{entity} filetype:bitmap',
        "gsrnamespace": 6,
        "gsrlimit": 6,
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "iiurlwidth": 1600,
        "format": "json",
    })
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _COMMONS_UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)

    NO_ATTRIBUTION_NEEDED = ("public domain", "pdm", "cc0", "godl-india")

    pages = (res.get("query") or {}).get("pages") or {}
    candidates = []
    for page in pages.values():
        title = page.get("title", "")
        if any(h in title.lower() for h in _LOGO_HINTS):
            continue
        relevance = _title_relevance(entity, title)
        if relevance < 1:
            continue  # title doesn't actually mention the subject — reject
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        if w < 700 or h < 500:  # filter out icon/thumbnail-sized files
            continue
        license_name = (info.get("extmetadata", {}).get("LicenseShortName", {})
                         .get("value", "")).lower()
        free_of_attribution = any(k in license_name for k in NO_ATTRIBUTION_NEEDED)
        candidates.append((relevance, free_of_attribution, w * h, info, title))
    if not candidates:
        raise RuntimeError(f"no relevant Commons photo for '{entity}'")

    # prefer: most relevant, then no-attribution-required, then highest-res
    candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    _, free_of_attribution, _, info, title = candidates[0]
    img_url = info.get("thumburl") or info["url"]
    meta = info.get("extmetadata", {})
    artist = "" if free_of_attribution else re.sub(
        "<[^<]+?>", "", meta.get("Artist", {}).get("value", "")).strip()

    req2 = urllib.request.Request(img_url, headers={"User-Agent": _COMMONS_UA})
    with urllib.request.urlopen(req2, timeout=30) as r:
        path = _save(r.read())
    return path, artist


def _openverse_search(query, entity=None, relevance_words=None, pool=8):
    """Shared by both the entity-search and topical-search stages. Filters
    to commercially-usable licenses via the API itself, then applies the
    same logo/relevance/license-preference rules used for Commons, plus a
    brand-safety check (mature content) Commons doesn't need.
    `relevance_words`, when given, requires the result's title to actually
    mention at least one of them — same purpose as `entity`, for the
    topical (non-named-subject) search path."""
    params = urllib.parse.urlencode({
        "q": query, "license_type": "commercial", "page_size": pool,
    })
    req = urllib.request.Request(
        f"https://api.openverse.org/v1/images/?{params}",
        headers={"User-Agent": _COMMONS_UA},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)

    NO_ATTRIBUTION_NEEDED = ("cc0", "publicdomain", "pdm")
    candidates = []
    for item in res.get("results", []):
        if item.get("mature"):
            continue
        title = item.get("title") or ""
        if any(h in title.lower() for h in _LOGO_HINTS):
            continue
        if entity and _title_relevance(entity, title) < 1:
            continue
        if relevance_words and _text_relevance(relevance_words, title) < 1:
            continue
        w, h = item.get("width", 0), item.get("height", 0)
        if w < 700 or h < 500:
            continue
        img_url = item.get("url")
        if not img_url:
            continue
        free = any(k in (item.get("license") or "").lower() for k in NO_ATTRIBUTION_NEEDED)
        candidates.append((free, w * h, img_url))
    if not candidates:
        raise RuntimeError(f"no usable Openverse results for '{query}'")

    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, img_url = candidates[0]
    req2 = urllib.request.Request(img_url, headers={"User-Agent": _COMMONS_UA})
    with urllib.request.urlopen(req2, timeout=30) as r:
        return _save(r.read())


def _openverse_entity(entity):
    return _openverse_search(entity, entity=entity), ""


def _openverse_topical(query, relevance_words=None):
    return _openverse_search(query, relevance_words=relevance_words)


def _search_query(story):
    cat = story["category"].replace(" NEWS", "")
    kws = _keywords(story["title"])
    return f"{cat} " + " ".join(kws) if kws else cat


def _broad_query(story):
    """Fallback query when the specific one finds nothing."""
    return story["category"].replace(" NEWS", "")


# Pexels/Unsplash both prohibit visible brand logos/trademarks/watermarks
# in contributed photos as part of their own submission guidelines, so we
# don't need to detect logos ourselves — it's already filtered upstream.

# Relevance ranking (the search API's own ordering) picks WHAT the photo is
# of; among that relevant set we then pick the visually strongest one, so
# the post doesn't always default to the plainest/first match.
RELEVANT_POOL = 8


# ---------- Pexels stock ----------
def _stock_pexels(query, relevance_words=None):
    key = settings.PEXELS_API_KEY
    if not key:
        raise RuntimeError("no pexels key")
    q = urllib.parse.quote(query)
    url = f"https://api.pexels.com/v1/search?query={q}&per_page={RELEVANT_POOL}&orientation=portrait"
    req = urllib.request.Request(url, headers={"Authorization": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    photos = res.get("photos", [])
    if not photos:
        raise RuntimeError(f"no pexels results for '{query}'")
    # the search API's own ranking can drift for niche/sensitive topics —
    # require the photo's own alt-text to actually mention the story
    # (when relevance_words given), THEN prefer the highest resolution
    if relevance_words:
        photos = [p for p in photos if _text_relevance(relevance_words, p.get("alt", "")) >= 1]
        if not photos:
            raise RuntimeError(f"no relevant pexels results for '{query}'")
    best = max(photos, key=lambda p: p.get("width", 0) * p.get("height", 0))
    img_url = best["src"]["large2x"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


# ---------- Unsplash stock ----------
def _stock_unsplash(query, relevance_words=None):
    key = settings.UNSPLASH_ACCESS_KEY
    if not key:
        raise RuntimeError("no unsplash key")
    q = urllib.parse.quote(query)
    url = (f"https://api.unsplash.com/search/photos?query={q}"
           f"&orientation=portrait&per_page={RELEVANT_POOL}&client_id={key}")
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)
    results = res.get("results", [])
    if not results:
        raise RuntimeError(f"no unsplash results for '{query}'")
    if relevance_words:
        def _desc(p):
            return f"{p.get('alt_description') or ''} {p.get('description') or ''}"
        results = [p for p in results if _text_relevance(relevance_words, _desc(p)) >= 1]
        if not results:
            raise RuntimeError(f"no relevant unsplash results for '{query}'")
    # among the relevant matches, prefer the most-liked (proxy for a more
    # striking, curiosity-grabbing photo rather than the plainest match)
    best = max(results, key=lambda p: p.get("likes", 0))
    img_url = best["urls"]["regular"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


def get_image(story):
    """Find a photo for the story; return path to a saved image.
    story is mutated with story['photo_credit'] when the photo came from
    a source with a known author (CC attribution)."""
    for entity in _proper_noun_phrases(story["title"]):
        for fn in (_wikimedia_commons, _openverse_entity):
            try:
                path, artist = fn(entity)
                print(f"image via {fn.__name__} (entity: '{entity}')")
                if artist:
                    story["photo_credit"] = artist
                return path
            except Exception as e:
                print(f"{fn.__name__} failed for '{entity}': {e}")

    # specific query: require the result to actually be about the story.
    # broad (category-only) query: no relevance check — it's already a
    # generic-but-safe fallback by construction (e.g. "Business" -> office
    # imagery), better as a last resort than failing outright.
    relevance_words = _keywords(story["title"])
    for query, words in ((_search_query(story), relevance_words),
                         (_broad_query(story), None)):
        for fn in (_stock_pexels, _stock_unsplash, _openverse_topical):
            try:
                path = fn(query, words)
                print(f"image via {fn.__name__} (query: '{query}')")
                return path
            except Exception as e:
                print(f"{fn.__name__} failed for '{query}': {e}")
    raise RuntimeError("ALL image sources failed")
