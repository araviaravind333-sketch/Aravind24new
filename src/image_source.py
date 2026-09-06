"""
Image Source
============
Gets a high-quality, LEGALLY SAFE photo for the story by searching real
stock-photo libraries (Pexels, then Unsplash) for keywords pulled straight
out of the headline — no AI image generation, so what's shown is always an
actual photograph, never a hallucinated scene.
All returned images are safe to post commercially. We NEVER scrape
publisher photos (copyright / account-ban risk).
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


def _search_query(story):
    cat = story["category"].replace(" NEWS", "")
    kws = _keywords(story["title"])
    return f"{cat} " + " ".join(kws) if kws else cat


def _broad_query(story):
    """Fallback query when the specific one finds nothing."""
    return story["category"].replace(" NEWS", "")


# ---------- Pexels stock ----------
def _stock_pexels(query):
    key = settings.PEXELS_API_KEY
    if not key:
        raise RuntimeError("no pexels key")
    q = urllib.parse.quote(query)
    url = f"https://api.pexels.com/v1/search?query={q}&per_page=5&orientation=portrait"
    req = urllib.request.Request(url, headers={"Authorization": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    photos = res.get("photos", [])
    if not photos:
        raise RuntimeError(f"no pexels results for '{query}'")
    img_url = photos[0]["src"]["large2x"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


# ---------- Unsplash stock ----------
def _stock_unsplash(query):
    key = settings.UNSPLASH_ACCESS_KEY
    if not key:
        raise RuntimeError("no unsplash key")
    q = urllib.parse.quote(query)
    url = (f"https://api.unsplash.com/search/photos?query={q}"
           f"&orientation=portrait&per_page=5&client_id={key}")
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)
    results = res.get("results", [])
    if not results:
        raise RuntimeError(f"no unsplash results for '{query}'")
    img_url = results[0]["urls"]["regular"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


def get_image(story):
    """Search real stock photos for the story; return path to a saved image."""
    queries = [_search_query(story), _broad_query(story)]
    for query in queries:
        for fn in (_stock_pexels, _stock_unsplash):
            try:
                path = fn(query)
                print(f"image via {fn.__name__} (query: '{query}')")
                return path
            except Exception as e:
                print(f"{fn.__name__} failed for '{query}': {e}")
    raise RuntimeError("ALL image sources failed")
